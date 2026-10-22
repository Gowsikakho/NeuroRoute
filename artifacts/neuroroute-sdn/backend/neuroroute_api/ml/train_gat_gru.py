from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import torch
from sqlalchemy import select
from sqlalchemy.orm import Session
from torch.utils.data import DataLoader, TensorDataset

from ..preprocessing import EXPERIMENT_DATA_DIR, load_edge_index
from ..storage import QueueSample, engine
from ..topologies import TOPOLOGIES
from .gat_gru import GATGRUOccupancyPredictor, mse_loss


def load_training_tensors(
    topology_id: str,
    sequence_length: int,
    forecast_steps: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    if topology_id not in TOPOLOGIES:
        raise ValueError(f"Unknown topology: {topology_id}")
    topology = TOPOLOGIES[topology_id]
    expected_switches = {f"s{index}" for index in range(topology.node_count)}
    grouped: dict[float, dict[str, tuple[float, float]]] = defaultdict(dict)
    with Session(engine) as session:
        records = session.scalars(
            select(QueueSample).order_by(QueueSample.timestamp, QueueSample.id)
        ).all()
        for sample in records:
            grouped[sample.timestamp][sample.switch_id] = (
                sample.ls_occupancy,
                sample.ts_occupancy,
            )

    frames = []
    for timestamp in sorted(grouped):
        frame = grouped[timestamp]
        if set(frame) != expected_switches:
            continue
        frames.append(
            [[frame[switch_id][0] / 100, frame[switch_id][1] / 100]
             for switch_id in sorted(expected_switches)]
        )

    required = sequence_length + forecast_steps
    if len(frames) < required:
        raise ValueError(
            f"Need at least {required} complete {topology.name} queue samples; "
            f"found {len(frames)}. Run a simulation first."
        )

    sequences: list[list] = []
    targets: list[list] = []
    for start in range(len(frames) - required + 1):
        sequences.append(frames[start : start + sequence_length])
        future = frames[start + sequence_length : start + required]
        # Target is total LS + TS occupancy for each switch and forecast step.
        targets.append(
            [
                [
                    future[step][node_index][0] + future[step][node_index][1]
                    for step in range(forecast_steps)
                ]
                for node_index in range(topology.node_count)
            ]
        )
    return (
        torch.tensor(sequences, dtype=torch.float32),
        torch.tensor(targets, dtype=torch.float32),
    )


def load_preprocessed_tensors(
    topology_id: str,
    horizon_k: int,
) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    """Load the chronological train/validation/test windows from Step 5."""
    if topology_id not in TOPOLOGIES:
        raise ValueError(f"Unknown topology: {topology_id}")
    if horizon_k not in {3, 5}:
        raise ValueError("The collected dataset contains forecast horizons k=3 and k=5.")
    windows_path = EXPERIMENT_DATA_DIR / topology_id / "windows.csv"
    if not windows_path.exists():
        raise ValueError(
            f"Preprocessed windows not found at {windows_path}. "
            "Run neuroroute_api.data_collection first."
        )

    grouped: dict[str, tuple[list, list]] = {
        "train": ([], []),
        "validation": ([], []),
        "test": ([], []),
    }
    with windows_path.open(newline="", encoding="utf-8") as windows_file:
        for row in csv.DictReader(windows_file):
            if row["topology"] != topology_id or int(row["horizon_k"]) != horizon_k:
                continue
            split = row["split"]
            if split not in grouped:
                raise ValueError(f"Unknown chronological split: {split}")
            inputs, targets = grouped[split]
            inputs.append(json.loads(row["x_json"]))
            targets.append(json.loads(row["target_json"]))

    result = {}
    for split, (inputs, targets) in grouped.items():
        if not inputs:
            raise ValueError(
                f"The {split} split has no windows for topology={topology_id}, "
                f"horizon_k={horizon_k}."
            )
        result[split] = (
            torch.tensor(inputs, dtype=torch.float32),
            torch.tensor(targets, dtype=torch.float32),
        )
    return result


def build_edge_index(topology_id: str) -> torch.Tensor:
    return torch.tensor(load_edge_index(topology_id), dtype=torch.long)


def train(
    topology_id: str,
    epochs: int,
    sequence_length: int,
    forecast_steps: int,
    batch_size: int,
    seed: int,
    horizon_k: int | None = None,
) -> Path:
    torch.manual_seed(seed)
    if horizon_k is None:
        sequences, targets = load_training_tensors(
            topology_id, sequence_length, forecast_steps
        )
        split_tensors = None
        model_forecast_steps = forecast_steps
    else:
        split_tensors = load_preprocessed_tensors(topology_id, horizon_k)
        sequences, targets = split_tensors["train"]
        model_forecast_steps = 1

    dataset = TensorDataset(sequences, targets)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=horizon_k is None,
        num_workers=0,
    )
    model = GATGRUOccupancyPredictor(
        input_features=2,
        forecast_steps=model_forecast_steps,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    edge_index = build_edge_index(topology_id)
    for epoch in range(epochs):
        total_loss = 0.0
        batch_count = 0
        model.train()
        for sequence_batch, target_batch in loader:
            predictions = torch.stack(
                [model(sequence, edge_index) for sequence in sequence_batch]
            )
            if horizon_k is not None:
                target_batch = target_batch.unsqueeze(-1)
            loss = mse_loss(predictions, target_batch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().item())
            batch_count += 1
        print(f"epoch {epoch + 1}/{epochs} mse={total_loss / batch_count:.6f}")

    if split_tensors is not None:
        model.eval()
        with torch.no_grad():
            for split in ("validation", "test"):
                split_sequences, split_targets = split_tensors[split]
                predictions = torch.stack(
                    [model(sequence, edge_index) for sequence in split_sequences]
                )
                print(
                    f"{split} mse="
                    f"{mse_loss(predictions, split_targets.unsqueeze(-1)).item():.6f}"
                )

    model_dir = Path(__file__).resolve().parents[2] / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"gat-gru-{topology_id}.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "topology_id": topology_id,
            "sequence_length": int(sequences.shape[1]),
            "forecast_steps": model_forecast_steps,
            "input_features": 2,
            "horizon_k": horizon_k,
        },
        model_path,
    )
    return model_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the optional GAT-GRU forecaster.")
    parser.add_argument("--topology", choices=sorted(TOPOLOGIES), default="demo-7")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--sequence-length", type=int, default=12)
    parser.add_argument("--forecast-steps", type=int, default=5)
    parser.add_argument("--horizon-k", type=int, choices=(3, 5))
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1:
        parser.error("epochs and batch-size must be positive.")
    path = train(
        args.topology,
        args.epochs,
        args.sequence_length,
        args.forecast_steps,
        args.batch_size,
        args.seed,
        args.horizon_k,
    )
    print(f"Saved GAT-GRU model: {path}")


if __name__ == "__main__":
    main()
