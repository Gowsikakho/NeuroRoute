from __future__ import annotations

import argparse
import csv
import html
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .simulation import AVAILABLE_ALGORITHMS, SimulationManager
from .storage import initialize_storage, save_experiment
from .topologies import TOPOLOGIES


def write_csv(rows: list[dict], output_path: Path) -> None:
    columns = [
        "topology",
        "algorithm",
        "duration_seconds",
        "mean_latency_ms",
        "throughput_mbps",
        "packet_loss_percent",
        "mean_queue_occupancy_percent",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_horizontal_bar_chart(
    rows: list[dict],
    metric_key: str,
    title: str,
    unit: str,
    output_path: Path,
) -> None:
    width = 960
    left = 250
    right = 55
    top = 68
    row_height = 34
    height = top + len(rows) * row_height + 44
    maximum = max((float(row[metric_key]) for row in rows), default=1.0) or 1.0
    plot_width = width - left - right
    palette = ["#3c91a5", "#a4aa38", "#5574a8", "#ce8952", "#5a9672", "#9267c6"]

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
        f"<title>{html.escape(title)}</title>",
        '<rect width="100%" height="100%" fill="#10171b"/>',
        f'<text x="{left}" y="34" fill="#ecf2f3" font-family="Arial,sans-serif" font-size="20" font-weight="700">{html.escape(title)}</text>',
    ]
    for tick in range(5):
        value = maximum * tick / 4
        x = left + plot_width * tick / 4
        svg.append(
            f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 28}" stroke="#34434a" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{x:.1f}" y="{height - 10}" text-anchor="middle" fill="#9aabb2" font-family="Arial,sans-serif" font-size="11">{value:.2f} {html.escape(unit)}</text>'
        )
    for index, row in enumerate(rows):
        y = top + index * row_height
        value = float(row[metric_key])
        bar_width = max(2, plot_width * value / maximum)
        label = f"{row['topology']} · {row['algorithm']}"
        svg.append(
            f'<text x="{left - 12}" y="{y + 16}" text-anchor="end" fill="#c5d0d4" font-family="Arial,sans-serif" font-size="12">{html.escape(label)}</text>'
        )
        svg.append(
            f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="20" rx="4" fill="{palette[index % len(palette)]}"/>'
        )
        svg.append(
            f'<text x="{min(left + bar_width + 7, width - 48):.1f}" y="{y + 15}" fill="#edf2f3" font-family="Arial,sans-serif" font-size="11">{value:.3f}</text>'
        )
    svg.append("</svg>")
    output_path.write_text("\n".join(svg), encoding="utf-8")


def run_suite(duration_seconds: int, seed: int, output_dir: Path) -> Path:
    initialize_storage()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    algorithms = sorted(AVAILABLE_ALGORITHMS)
    for topology_id, topology in TOPOLOGIES.items():
        results = []
        for offset, algorithm in enumerate(algorithms):
            result = SimulationManager.run_offline(
                topology_id,
                algorithm,
                duration_seconds,
                seed + offset,
            )
            results.append(result)
            rows.append(
                {
                    "topology": topology.name,
                    "algorithm": algorithm,
                    "duration_seconds": duration_seconds,
                    "mean_latency_ms": result["meanLatencyMs"],
                    "throughput_mbps": result["throughputMbps"],
                    "packet_loss_percent": result["packetLossPercent"],
                    "mean_queue_occupancy_percent": result["meanQueueOccupancy"],
                }
            )
        now = datetime.now(timezone.utc).isoformat()
        save_experiment(
            {
                "id": str(uuid.uuid4()),
                "topologyId": topology_id,
                "topologyName": topology.name,
                "status": "completed",
                "algorithms": algorithms,
                "durationSeconds": duration_seconds,
                "createdAt": now,
                "completedAt": now,
                "results": results,
            }
        )

    csv_path = output_dir / "experiment-results.csv"
    write_csv(rows, csv_path)
    chart_specs = [
        ("mean_latency_ms", "Mean latency by topology and algorithm", "ms", "mean-latency.svg"),
        ("throughput_mbps", "Throughput by topology and algorithm", "Mbps", "throughput.svg"),
        ("packet_loss_percent", "Packet loss by topology and algorithm", "%", "packet-loss.svg"),
        ("mean_queue_occupancy_percent", "Mean queue occupancy by topology and algorithm", "%", "queue-occupancy.svg"),
    ]
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    for key, title, unit, filename in chart_specs:
        write_horizontal_bar_chart(rows, key, title, unit, charts_dir / filename)
    return csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the native NeuroRoute experiment suite.")
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "generated-artifacts",
    )
    arguments = parser.parse_args()
    if not 10 <= arguments.duration_seconds <= 3600:
        parser.error("duration-seconds must be between 10 and 3600.")
    csv_path = run_suite(
        arguments.duration_seconds,
        arguments.seed,
        arguments.output_dir.resolve(),
    )
    print(f"Experiment CSV: {csv_path}")
    print(f"Charts: {arguments.output_dir.resolve() / 'charts'}")


if __name__ == "__main__":
    main()
