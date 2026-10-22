from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from .preprocessing import (
    EXPERIMENT_DATA_DIR,
    dense_timing_indicator,
    edge_index_array,
    save_edge_index,
)
from .queue_model import QUEUE_CAPACITY_BYTES
from .simulation import (
    CONGESTION_THRESHOLD_PERCENT,
    TS_PACKET_BYTES,
    SimulationManager,
)
from .topologies import TOPOLOGIES

TOPOLOGY_ID = "demo-7"
ALGORITHM = "static-dijkstra"
WINDOW_LENGTH = 10
FORECAST_HORIZONS = (3, 5)
Y_MAX = 10.0
MINIMUM_RAW_ROWS = 200_000
RAW_COLUMNS = (
    "run_id",
    "topology",
    "scenario",
    "algorithm",
    "seed",
    "elapsed_seconds",
    "switch_id",
    "queue_length",
    "queue_bytes",
    "queue_capacity_packets",
    "c_th",
    "congestion_state",
)
WINDOW_COLUMNS = (
    "run_id",
    "topology",
    "scenario",
    "seed",
    "horizon_k",
    "split",
    "input_start_index",
    "target_index",
    "x_json",
    "target_json",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    ls_rate: float
    ts_rate: float
    traffic_profile: str = "normal"


SCENARIOS = (
    Scenario("low", ls_rate=1.0, ts_rate=2.0),
    Scenario("medium", ls_rate=4.0, ts_rate=8.0),
    Scenario("bursty", ls_rate=3.0, ts_rate=8.0, traffic_profile="bursty"),
    Scenario("hotspot", ls_rate=5.0, ts_rate=15.0, traffic_profile="hotspot"),
)


def build_run_windows(
    run_id: str,
    scenario: str,
    seed: int,
    queue_frames: Sequence[dict[str, dict]],
    window_length: int = WINDOW_LENGTH,
    horizons: Sequence[int] = FORECAST_HORIZONS,
    y_max: float = Y_MAX,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> Iterator[dict]:
    """Yield chronological graph windows without crossing independent runs."""
    if window_length < 1 or any(horizon < 1 for horizon in horizons):
        raise ValueError("Window length and forecast horizons must be positive.")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between 0 and 1.")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train and validation fractions must sum to less than 1.")
    if not queue_frames:
        return

    switch_ids = sorted(queue_frames[0])
    if not switch_ids or any(set(frame) != set(switch_ids) for frame in queue_frames):
        raise ValueError("Every time frame must contain the same switches.")

    queue_series = {
        switch_id: [int(frame[switch_id]["queue_length"]) for frame in queue_frames]
        for switch_id in switch_ids
    }
    thresholds = {
        switch_id: [
            float(frame[switch_id]["c_th"])
            for frame in queue_frames
        ]
        for switch_id in switch_ids
    }
    timing_indicators = {
        switch_id: dense_timing_indicator(
            queue_series[switch_id],
            thresholds[switch_id],
            y_max,
        )
        for switch_id in switch_ids
    }

    frame_count = len(queue_frames)
    train_end = math.floor(frame_count * train_fraction)
    validation_end = math.floor(
        frame_count * (train_fraction + validation_fraction)
    )

    for horizon in horizons:
        last_start = frame_count - window_length - horizon + 1
        for start_index in range(max(0, last_start)):
            target_index = start_index + window_length - 1 + horizon
            if target_index < train_end:
                split = "train"
            elif target_index < validation_end:
                split = "validation"
            else:
                split = "test"

            x = [
                [
                    [
                        queue_series[switch_id][time_index],
                        timing_indicators[switch_id][time_index],
                    ]
                    for switch_id in switch_ids
                ]
                for time_index in range(start_index, start_index + window_length)
            ]
            target = [
                int(
                    queue_series[switch_id][target_index]
                    > thresholds[switch_id][target_index]
                )
                for switch_id in switch_ids
            ]
            yield {
                "run_id": run_id,
                "scenario": scenario,
                "seed": seed,
                "horizon_k": horizon,
                "split": split,
                "input_start_index": start_index,
                "target_index": target_index,
                "x": x,
                "target": target,
                "switch_ids": switch_ids,
            }


def _frames_from_samples(samples: Sequence[dict]) -> list[dict[str, dict]]:
    samples_by_time: dict[int, dict[str, dict]] = {}
    for sample in samples:
        elapsed = int(sample["elapsedSeconds"])
        frame = samples_by_time.setdefault(elapsed, {})
        frame[sample["switchId"]] = {
            "queue_length": int(sample["queueLength"]),
            "c_th": float(sample["congestionThreshold"]),
        }
    if not samples_by_time:
        return []
    expected_switches = {f"s{index}" for index in range(TOPOLOGIES[TOPOLOGY_ID].node_count)}
    frames = []
    for elapsed in sorted(samples_by_time):
        frame = samples_by_time[elapsed]
        if set(frame) != expected_switches:
            raise ValueError(f"Incomplete switch sample at elapsed second {elapsed}.")
        frames.append(frame)
    return frames


def collect_dataset(
    output_dir: Path = EXPERIMENT_DATA_DIR / TOPOLOGY_ID,
    seed_count: int = 10,
    duration_seconds: int | None = None,
    minimum_rows: int = MINIMUM_RAW_ROWS,
) -> dict:
    if seed_count < 1 or minimum_rows < 1:
        raise ValueError("seed_count and minimum_rows must be positive.")
    topology = TOPOLOGIES[TOPOLOGY_ID]
    if duration_seconds is None:
        duration_seconds = math.ceil(
            minimum_rows
            / (topology.node_count * len(SCENARIOS) * seed_count)
        )
    minimum_window_seconds = WINDOW_LENGTH + max(FORECAST_HORIZONS)
    if duration_seconds < minimum_window_seconds:
        raise ValueError(
            f"duration_seconds must be at least {minimum_window_seconds} "
            "to produce both forecast horizons."
        )
    estimated_rows = (
        duration_seconds * topology.node_count * len(SCENARIOS) * seed_count
    )
    if estimated_rows < minimum_rows:
        raise ValueError(
            f"Configuration would create {estimated_rows:,} rows; "
            f"at least {minimum_rows:,} are required."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "queue_samples.csv"
    windows_path = output_dir / "windows.csv"
    expected_edge_path = output_dir / "edge_index.json"
    if expected_edge_path.resolve() == (
        EXPERIMENT_DATA_DIR / TOPOLOGY_ID / "edge_index.json"
    ).resolve():
        edge_path = save_edge_index(TOPOLOGY_ID)
    else:
        edge_path = output_dir / "edge_index.json"
        edge_path.write_text(
            json.dumps(edge_index_array(TOPOLOGY_ID), separators=(",", ":")),
            encoding="utf-8",
        )

    split_counts = {
        str(horizon): {"train": 0, "validation": 0, "test": 0}
        for horizon in FORECAST_HORIZONS
    }
    positive_state_counts: dict[str, int] = {}
    positive_target_counts = {
        str(horizon): {"train": 0, "validation": 0, "test": 0}
        for horizon in FORECAST_HORIZONS
    }
    raw_row_count = 0
    run_count = 0
    with (
        raw_path.open("w", newline="", encoding="utf-8") as raw_file,
        windows_path.open("w", newline="", encoding="utf-8") as windows_file,
    ):
        raw_writer = csv.DictWriter(raw_file, fieldnames=RAW_COLUMNS)
        windows_writer = csv.DictWriter(windows_file, fieldnames=WINDOW_COLUMNS)
        raw_writer.writeheader()
        windows_writer.writeheader()

        for scenario in SCENARIOS:
            for seed in range(1, seed_count + 1):
                run_id = f"step5-{scenario.name}-seed-{seed:02d}"
                result = SimulationManager.run_offline(
                    TOPOLOGY_ID,
                    ALGORITHM,
                    duration_seconds,
                    seed,
                    ls_rate=scenario.ls_rate,
                    ts_rate=scenario.ts_rate,
                    capture_switch_queues=True,
                    traffic_profile=scenario.traffic_profile,
                    static_routing_only=True,
                )
                samples = result["switchQueueSamples"]
                expected_samples = duration_seconds * topology.node_count
                if len(samples) != expected_samples:
                    raise RuntimeError(
                        f"{run_id}: expected {expected_samples} switch samples, "
                        f"received {len(samples)}."
                    )
                for sample in samples:
                    threshold = float(sample["congestionThreshold"])
                    queue_length = int(sample["queueLength"])
                    congestion_state = int(queue_length > threshold)
                    raw_writer.writerow(
                        {
                            "run_id": run_id,
                            "topology": TOPOLOGY_ID,
                            "scenario": scenario.name,
                            "algorithm": ALGORITHM,
                            "seed": seed,
                            "elapsed_seconds": int(sample["elapsedSeconds"]),
                            "switch_id": sample["switchId"],
                            "queue_length": queue_length,
                            "queue_bytes": int(sample["queueBytes"]),
                            "queue_capacity_packets": int(
                                sample["queueCapacityPackets"]
                            ),
                            "c_th": threshold,
                            "congestion_state": congestion_state,
                        }
                    )
                    positive_state_counts[scenario.name] = (
                        positive_state_counts.get(scenario.name, 0)
                        + congestion_state
                    )
                raw_row_count += len(samples)

                frames = _frames_from_samples(samples)
                for window in build_run_windows(
                    run_id,
                    scenario.name,
                    seed,
                    frames,
                ):
                    horizon = str(window["horizon_k"])
                    split = window["split"]
                    split_counts[horizon][split] += 1
                    positive_target_counts[horizon][split] += sum(window["target"])
                    windows_writer.writerow(
                        {
                            "run_id": window["run_id"],
                            "topology": TOPOLOGY_ID,
                            "scenario": window["scenario"],
                            "seed": window["seed"],
                            "horizon_k": window["horizon_k"],
                            "split": window["split"],
                            "input_start_index": window["input_start_index"],
                            "target_index": window["target_index"],
                            "x_json": json.dumps(
                                window["x"], separators=(",", ":")
                            ),
                            "target_json": json.dumps(
                                window["target"], separators=(",", ":")
                            ),
                        }
                    )
                run_count += 1
                print(
                    f"Collected {run_id}: {len(samples):,} switch-second rows "
                    f"({run_count}/{len(SCENARIOS) * seed_count} runs).",
                    flush=True,
                )

    if raw_row_count < minimum_rows:
        raise RuntimeError(
            f"Only {raw_row_count:,} rows were collected; "
            f"{minimum_rows:,} are required."
        )

    metadata = {
        "topology": TOPOLOGY_ID,
        "algorithm": ALGORITHM,
        "raw_rows": raw_row_count,
        "run_count": run_count,
        "duration_seconds_per_run": duration_seconds,
        "switch_order": [f"s{index}" for index in range(topology.node_count)],
        "queue_length_definition": (
            "Number of LS and TS packets currently queued across all egress ports "
            "of a switch at the one-second poll."
        ),
        "c_th_definition": (
            "80 percent of one TS FIFO's maximum packet capacity, in packets. "
            "This provides a switch-level threshold sensitive to a saturated "
            "egress queue instead of masking it in aggregate multi-port capacity."
        ),
        "c_th_packets": (
            QUEUE_CAPACITY_BYTES // TS_PACKET_BYTES
        )
        * CONGESTION_THRESHOLD_PERCENT
        / 100,
        "eq_3": (
            "y_n=y_(n-1)+1 when queue_length<=C_th, otherwise y_n=0; "
            "timing feature=tanh(y_n/y_max)."
        ),
        "y_max": Y_MAX,
        "window_length": WINDOW_LENGTH,
        "forecast_horizons": list(FORECAST_HORIZONS),
        "split": {
            "train": 0.70,
            "validation": 0.15,
            "test": 0.15,
            "assignment": "Chronological by target index within each run; no shuffling.",
            "window_counts_by_horizon": split_counts,
        },
        "features": ["queue_length_packets", "dense_timing_indicator"],
        "target": "Binary congestion state at the future target index.",
        "positive_congestion_samples_by_scenario": positive_state_counts,
        "positive_targets_by_horizon_and_split": positive_target_counts,
        "scenarios": [
            {
                "name": scenario.name,
                "ls_rate_percent": scenario.ls_rate,
                "ts_rate_percent": scenario.ts_rate,
                "traffic_profile": scenario.traffic_profile,
            }
            for scenario in SCENARIOS
        ],
        "files": {
            "raw_samples": raw_path.name,
            "windows": windows_path.name,
            "edge_index": edge_path.name,
        },
        "edge_index_shape": [2, len(edge_index_array(TOPOLOGY_ID)[0])],
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def reprocess_saved_samples(
    output_dir: Path = EXPERIMENT_DATA_DIR / TOPOLOGY_ID,
) -> dict:
    """Recompute Eq. 3 features and windows from an existing raw CSV."""
    raw_path = output_dir / "queue_samples.csv"
    windows_path = output_dir / "windows.csv"
    metadata_path = output_dir / "metadata.json"
    if not raw_path.exists():
        raise ValueError(f"Raw queue samples not found at {raw_path}.")
    ts_fifo_capacity_packets = QUEUE_CAPACITY_BYTES // TS_PACKET_BYTES
    c_th = ts_fifo_capacity_packets * CONGESTION_THRESHOLD_PERCENT / 100
    raw_temp = raw_path.with_suffix(".csv.tmp")
    windows_temp = windows_path.with_suffix(".csv.tmp")
    split_counts = {
        str(horizon): {"train": 0, "validation": 0, "test": 0}
        for horizon in FORECAST_HORIZONS
    }
    positive_state_counts: dict[str, int] = {}
    positive_target_counts = {
        str(horizon): {"train": 0, "validation": 0, "test": 0}
        for horizon in FORECAST_HORIZONS
    }
    raw_row_count = 0

    with (
        raw_path.open(newline="", encoding="utf-8") as source,
        raw_temp.open("w", newline="", encoding="utf-8") as raw_output,
        windows_temp.open("w", newline="", encoding="utf-8") as windows_output,
    ):
        raw_reader = csv.DictReader(source)
        raw_writer = csv.DictWriter(raw_output, fieldnames=RAW_COLUMNS)
        windows_writer = csv.DictWriter(windows_output, fieldnames=WINDOW_COLUMNS)
        raw_writer.writeheader()
        windows_writer.writeheader()
        for run_id, grouped_rows in itertools.groupby(
            raw_reader,
            key=lambda row: row["run_id"],
        ):
            rows = list(grouped_rows)
            if not rows:
                continue
            scenario = rows[0]["scenario"]
            seed = int(rows[0]["seed"])
            samples = []
            for row in rows:
                queue_length = int(row["queue_length"])
                row["c_th"] = c_th
                row["congestion_state"] = int(queue_length > c_th)
                raw_writer.writerow(row)
                raw_row_count += 1
                positive_state_counts[scenario] = (
                    positive_state_counts.get(scenario, 0)
                    + int(row["congestion_state"])
                )
                samples.append(
                    {
                        "switchId": row["switch_id"],
                        "elapsedSeconds": int(row["elapsed_seconds"]),
                        "queueLength": queue_length,
                        "congestionThreshold": c_th,
                    }
                )

            frames = _frames_from_samples(samples)
            for window in build_run_windows(
                run_id,
                scenario,
                seed,
                frames,
            ):
                horizon = str(window["horizon_k"])
                split = window["split"]
                split_counts[horizon][split] += 1
                positive_target_counts[horizon][split] += sum(window["target"])
                windows_writer.writerow(
                    {
                        "run_id": window["run_id"],
                        "topology": TOPOLOGY_ID,
                        "scenario": window["scenario"],
                        "seed": window["seed"],
                        "horizon_k": window["horizon_k"],
                        "split": split,
                        "input_start_index": window["input_start_index"],
                        "target_index": window["target_index"],
                        "x_json": json.dumps(
                            window["x"], separators=(",", ":")
                        ),
                        "target_json": json.dumps(
                            window["target"], separators=(",", ":")
                        ),
                    }
                )

    if raw_row_count == 0:
        raw_temp.unlink(missing_ok=True)
        windows_temp.unlink(missing_ok=True)
        raise ValueError(f"No queue samples found in {raw_path}.")
    raw_temp.replace(raw_path)
    windows_temp.replace(windows_path)

    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    else:
        metadata = {
            "topology": TOPOLOGY_ID,
            "algorithm": ALGORITHM,
            "run_count": len(positive_state_counts),
            "duration_seconds_per_run": None,
            "switch_order": [
                f"s{index}" for index in range(TOPOLOGIES[TOPOLOGY_ID].node_count)
            ],
            "window_length": WINDOW_LENGTH,
            "forecast_horizons": list(FORECAST_HORIZONS),
            "features": ["queue_length_packets", "dense_timing_indicator"],
            "target": "Binary congestion state at the future target index.",
        }
    metadata.update(
        {
            "raw_rows": raw_row_count,
            "c_th_definition": (
                "80 percent of one TS FIFO's maximum packet capacity, in packets. "
                "This provides a switch-level threshold sensitive to a saturated "
                "egress queue instead of masking it in aggregate multi-port capacity."
            ),
            "c_th_packets": c_th,
            "eq_3": (
                "y_n=y_(n-1)+1 when queue_length<=C_th, otherwise y_n=0; "
                "timing feature=tanh(y_n/y_max)."
            ),
            "y_max": Y_MAX,
            "split": {
                "train": 0.70,
                "validation": 0.15,
                "test": 0.15,
                "assignment": (
                    "Chronological by target index within each run; no shuffling."
                ),
                "window_counts_by_horizon": split_counts,
            },
            "positive_congestion_samples_by_scenario": positive_state_counts,
            "positive_targets_by_horizon_and_split": positive_target_counts,
            "files": {
                "raw_samples": raw_path.name,
                "windows": windows_path.name,
                "edge_index": "edge_index.json",
            },
            "edge_index_shape": [
                2,
                len(edge_index_array(TOPOLOGY_ID)[0]),
            ],
        }
    )
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect static-routing queue data and create time-split windows."
    )
    parser.add_argument("--seed-count", type=int, default=10)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--minimum-rows", type=int, default=MINIMUM_RAW_ROWS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXPERIMENT_DATA_DIR / TOPOLOGY_ID,
    )
    parser.add_argument(
        "--preprocess-existing",
        action="store_true",
        help="Recompute labels and windows from an existing queue_samples.csv.",
    )
    args = parser.parse_args()
    if args.seed_count < 1 or args.minimum_rows < 1:
        parser.error("seed-count and minimum-rows must be positive.")
    try:
        if args.preprocess_existing:
            metadata = reprocess_saved_samples(args.output_dir)
        else:
            metadata = collect_dataset(
                output_dir=args.output_dir,
                seed_count=args.seed_count,
                duration_seconds=args.duration_seconds,
                minimum_rows=args.minimum_rows,
            )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(
        f"Saved {metadata['raw_rows']:,} raw rows, "
        f"{metadata['run_count']} runs, windows, and edge_index under "
        f"{args.output_dir.resolve()}."
    )


if __name__ == "__main__":
    main()
