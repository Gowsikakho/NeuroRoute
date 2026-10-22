from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import networkx as nx
import torch
from sqlalchemy import select
from sqlalchemy.orm import Session
from torch.utils.data import DataLoader, TensorDataset

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


def build_edge_index(topology_id: str) -> torch.Tensor:
    topology = TOPOLOGIES[topology_id]
    graph = nx.Graph()
    graph.add_nodes_from(range(topology.node_count))
    graph.add_edges_from(topology.edges)
    edges = []
    for source, target in graph.edges:
        edges.extend(((source, target), (target, source)))
    return torch.tensor(edges, dtype=torch.long).t().contiguous()


def train(
    topology_id: str,
    epochs: int,
    sequence_length: int,
    forecast_steps: int,
    batch_size: int,
    seed: int,
) -> Path:
    torch.manual_seed(seed)
    sequences, targets = load_training_tensors(
        topology_id, sequence_length, forecast_steps
    )
    dataset = TensorDataset(sequences, targets)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=0,
    )
    model = GATGRUOccupancyPredictor(
        input_features=2,
        forecast_steps=forecast_steps,
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
            loss = mse_loss(predictions, target_batch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().item())
            batch_count += 1
        print(f"epoch {epoch + 1}/{epochs} mse={total_loss / batch_count:.6f}")

    model_dir = Path(__file__).resolve().parents[2] / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"gat-gru-{topology_id}.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "topology_id": topology_id,
            "sequence_length": sequence_length,
            "forecast_steps": forecast_steps,
            "input_features": 2,
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
    )
    print(f"Saved GAT-GRU model: {path}")


if __name__ == "__main__":
    main()
