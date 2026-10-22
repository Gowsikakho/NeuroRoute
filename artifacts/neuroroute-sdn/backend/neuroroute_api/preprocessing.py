from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

from .topologies import TOPOLOGIES


PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_DATA_DIR = PROJECT_ROOT / "experiments" / "data"


def dense_timing_indicator(
    queue_lengths: Sequence[float],
    c_th: float | Sequence[float],
    y_max: float,
) -> list[float]:
    """Implement Eq. 3 over a time-ordered queue-length series."""
    if y_max <= 0:
        raise ValueError("y_max must be positive.")
    if isinstance(c_th, Sequence) and not isinstance(c_th, (str, bytes)):
        thresholds = list(c_th)
        if len(thresholds) != len(queue_lengths):
            raise ValueError("Per-sample thresholds must match queue_lengths.")
    else:
        thresholds = [float(c_th)] * len(queue_lengths)

    indicator = 0
    result: list[float] = []
    for queue_length, threshold in zip(queue_lengths, thresholds):
        if not math.isfinite(float(queue_length)) or not math.isfinite(float(threshold)):
            raise ValueError("Queue lengths and thresholds must be finite.")
        indicator = indicator + 1 if queue_length <= threshold else 0
        result.append(math.tanh(indicator / y_max))
    return result


def tanh_normalize(
    values: Sequence[float],
    center: float,
    scale: float,
) -> list[float]:
    """Apply configurable tanh normalization; paper constants are not assumed."""
    if scale <= 0:
        raise ValueError("normalization scale must be positive.")
    return [math.tanh((value - center) / scale) for value in values]


def edge_index_array(topology_id: str) -> list[list[int]]:
    """Return a bidirectional graph edge index in [2, directed_edges] layout."""
    if topology_id not in TOPOLOGIES:
        raise ValueError(f"Unknown topology: {topology_id}")
    sources: list[int] = []
    targets: list[int] = []
    for source, target in TOPOLOGIES[topology_id].edges:
        sources.extend((source, target))
        targets.extend((target, source))
    return [sources, targets]


def edge_index_path(topology_id: str) -> Path:
    return EXPERIMENT_DATA_DIR / topology_id / "edge_index.json"


def save_edge_index(topology_id: str) -> Path:
    path = edge_index_path(topology_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(edge_index_array(topology_id), separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def load_edge_index(topology_id: str) -> list[list[int]]:
    path = edge_index_path(topology_id)
    if not path.exists():
        return edge_index_array(topology_id)
    edge_index = json.loads(path.read_text(encoding="utf-8"))
    expected_shape = len(TOPOLOGIES[topology_id].edges) * 2
    if (
        not isinstance(edge_index, list)
        or len(edge_index) != 2
        or any(not isinstance(row, list) or len(row) != expected_shape for row in edge_index)
    ):
        raise ValueError(f"Invalid saved edge_index array: {path}")
    return edge_index
