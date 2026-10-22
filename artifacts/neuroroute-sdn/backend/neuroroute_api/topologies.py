from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TopologyDefinition:
    id: str
    name: str
    description: str
    node_count: int
    edges: tuple[tuple[int, int], ...]
    bandwidth_mbps: float
    delay_ms: float

    def as_dict(self, link_utilization: dict[str, float] | None = None) -> dict:
        radius = 39
        center_x = center_y = 50
        nodes = []
        for index in range(self.node_count):
            angle = -math.pi / 2 + (2 * math.pi * index / self.node_count)
            nodes.append(
                {
                    "id": f"s{index}",
                    "label": f"S{index}",
                    "x": round(center_x + radius * math.cos(angle), 2),
                    "y": round(center_y + radius * math.sin(angle), 2),
                }
            )

        links = []
        for index, (source, target) in enumerate(self.edges):
            link_id = f"s{source}-s{target}"
            links.append(
                {
                    "id": link_id,
                    "source": f"s{source}",
                    "target": f"s{target}",
                    "bandwidthMbps": self.bandwidth_mbps,
                    "delayMs": self.delay_ms + ((index * 7) % 18),
                    "utilization": round((link_utilization or {}).get(link_id, 0), 1),
                }
            )
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "nodeCount": self.node_count,
            "linkCount": len(self.edges),
            "nodes": nodes,
            "links": links,
        }


TOPOLOGIES: dict[str, TopologyDefinition] = {
    "nsfnet": TopologyDefinition(
        id="nsfnet",
        name="NSFNET",
        description="14-node, 21-link research backbone topology.",
        node_count=14,
        edges=(
            (0, 1), (0, 2), (0, 7), (1, 2), (1, 3), (2, 5), (3, 4),
            (3, 10), (4, 5), (4, 6), (5, 9), (5, 13), (6, 7), (6, 8),
            (7, 10), (8, 9), (8, 11), (9, 12), (10, 11), (10, 12), (11, 13),
        ),
        bandwidth_mbps=1000,
        delay_ms=8,
    ),
    "abilene": TopologyDefinition(
        id="abilene",
        name="Abilene",
        description="11-node backbone topology used in wide-area network studies.",
        node_count=11,
        edges=(
            (0, 1), (0, 2), (1, 2), (1, 3), (2, 4), (3, 4), (3, 5),
            (4, 6), (5, 6), (5, 7), (6, 8), (7, 8), (7, 9), (8, 10), (9, 10),
        ),
        bandwidth_mbps=1000,
        delay_ms=12,
    ),
    "demo-7": TopologyDefinition(
        id="demo-7",
        name="7-switch demo",
        description="A compact 7-switch topology for quick experiments.",
        node_count=7,
        edges=(
            (0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 0),
            (0, 2), (1, 4), (2, 5), (3, 6),
        ),
        bandwidth_mbps=1000,
        delay_ms=3,
    ),
}
