from __future__ import annotations

import csv
from pathlib import Path


RUN_COLUMNS = (
    "run_id",
    "topology",
    "scenario",
    "algorithm",
    "seed",
    "ls_latency_ms",
    "ts_latency_ms",
    "ls_loss_pct",
    "ts_loss_pct",
    "qos_satisfied_pct",
    "reroutes",
    "proactive_reroutes",
    "reactive_reroutes",
)

BASELINE_RUNS_PATH = (
    Path(__file__).resolve().parents[4] / "experiments" / "results" / "runs.csv"
)
NUMERIC_COLUMNS = frozenset(RUN_COLUMNS[4:])
INTEGER_COLUMNS = frozenset(
    {"seed", "reroutes", "proactive_reroutes", "reactive_reroutes"}
)


def write_baseline_runs(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def load_baseline_runs(path: Path = BASELINE_RUNS_PATH) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Baseline results CSV not found: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != RUN_COLUMNS:
            raise ValueError(
                "Baseline results CSV has an unexpected header; "
                f"expected {','.join(RUN_COLUMNS)}."
            )

        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(row.get(column) in (None, "") for column in RUN_COLUMNS):
                raise ValueError(
                    f"Baseline results CSV has an incomplete row at line {line_number}."
                )
            parsed = dict(row)
            for column in NUMERIC_COLUMNS:
                try:
                    parsed[column] = (
                        int(row[column])
                        if column in INTEGER_COLUMNS
                        else float(row[column])
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"Baseline results CSV has an invalid {column} value "
                        f"at line {line_number}."
                    ) from error
            rows.append(parsed)
    return rows
