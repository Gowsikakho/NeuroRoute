from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from .baseline_results import BASELINE_RUNS_PATH, write_baseline_runs
from .simulation import SimulationManager
from .topologies import TOPOLOGIES

SCENARIO_ID = "E1"
SCENARIO_TOPOLOGY = "demo-7"
SCENARIO_DURATION_SECONDS = 30
SCENARIO_LS_RATE = 35
SCENARIO_TS_RATE = 50
SCENARIO_ALGORITHMS = ("static-dijkstra", "ecmp")
SCENARIO_SEEDS = range(1, 11)


def run_baseline_suite(output_path: Path = BASELINE_RUNS_PATH) -> list[dict]:
    topology = TOPOLOGIES[SCENARIO_TOPOLOGY]
    rows: list[dict] = []
    for algorithm in SCENARIO_ALGORITHMS:
        for seed in SCENARIO_SEEDS:
            result = SimulationManager.run_offline(
                SCENARIO_TOPOLOGY,
                algorithm,
                SCENARIO_DURATION_SECONDS,
                seed,
                ls_rate=SCENARIO_LS_RATE,
                ts_rate=SCENARIO_TS_RATE,
            )
            reroutes = int(result["reroutes"])
            rows.append(
                {
                    "run_id": str(uuid.uuid4()),
                    "topology": topology.id,
                    "scenario": SCENARIO_ID,
                    "algorithm": algorithm,
                    "seed": seed,
                    "ls_latency_ms": result["lsLatencyMs"],
                    "ts_latency_ms": result["tsLatencyMs"],
                    "ls_loss_pct": result["lsLossPct"],
                    "ts_loss_pct": result["tsLossPct"],
                    "qos_satisfied_pct": result["qosSatisfiedPct"],
                    "reroutes": reroutes,
                    "proactive_reroutes": 0,
                    "reactive_reroutes": reroutes,
                }
            )
    write_baseline_runs(rows, output_path)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run E1 normal-load static Dijkstra and ECMP baselines."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASELINE_RUNS_PATH,
        help="CSV output path (defaults to experiments/results/runs.csv).",
    )
    arguments = parser.parse_args()
    rows = run_baseline_suite(arguments.output.resolve())
    print(f"Wrote {len(rows)} baseline runs to {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
