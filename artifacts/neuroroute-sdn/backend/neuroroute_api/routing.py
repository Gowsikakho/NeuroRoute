from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

import networkx as nx


@dataclass(frozen=True)
class FlowDecision:
    """A route and LS queue service share selected for one flow."""

    path: list[str]
    queue_rate: float


class RoutingAlgorithm(ABC):
    """A routing policy that maps an observed network state to per-flow decisions."""

    @abstractmethod
    def select_path(
        self,
        graph: nx.Graph,
        flow_id: str,
        source: str,
        destination: str,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def decide(self, state: dict) -> dict[str, FlowDecision]:
        raise NotImplementedError


def _graph_from_state(state: dict) -> nx.Graph:
    graph = nx.Graph()
    topology = state.get("topology", {})
    graph.add_nodes_from(node["id"] for node in topology.get("nodes", []))
    for link in topology.get("links", []):
        if not link.get("isUp", True):
            continue
        graph.add_edge(
            link["source"],
            link["target"],
            delay=float(link.get("delayMs", 1.0)),
            weight=1.0,
        )
    return graph


class StaticRoutingAlgorithm(RoutingAlgorithm):
    """Select a minimum-hop route and preserve the default LS/TS split."""

    def select_path(
        self,
        graph: nx.Graph,
        flow_id: str,
        source: str,
        destination: str,
    ) -> list[str]:
        del flow_id
        try:
            return nx.shortest_path(graph, source, destination, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def decide(self, state: dict) -> dict[str, FlowDecision]:
        graph = _graph_from_state(state)
        decisions: dict[str, FlowDecision] = {}
        for flow in state.get("flows", []):
            path = self.select_path(
                graph,
                flow["flowId"],
                flow["source"],
                flow["destination"],
            )
            decisions[flow["flowId"]] = FlowDecision(path=path, queue_rate=0.62)
        return decisions


class EcmpRoutingAlgorithm(RoutingAlgorithm):
    """Hash each flow onto a deterministic equal-minimum-delay path."""

    def select_path(
        self,
        graph: nx.Graph,
        flow_id: str,
        source: str,
        destination: str,
    ) -> list[str]:
        try:
            paths = sorted(
                tuple(path)
                for path in nx.all_shortest_paths(
                    graph,
                    source,
                    destination,
                    weight="weight",
                )
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
        if not paths:
            return []
        digest = hashlib.sha256(flow_id.encode("utf-8")).digest()
        return list(paths[int.from_bytes(digest[:8], "big") % len(paths)])

    def decide(self, state: dict) -> dict[str, FlowDecision]:
        graph = _graph_from_state(state)
        decisions: dict[str, FlowDecision] = {}
        for flow in state.get("flows", []):
            path = self.select_path(
                graph,
                flow["flowId"],
                flow["source"],
                flow["destination"],
            )
            decisions[flow["flowId"]] = FlowDecision(path=path, queue_rate=0.62)
        return decisions


@dataclass(frozen=True)
class AlgorithmRegistration:
    name: str
    factory: Callable[[], RoutingAlgorithm] | None
    display_name: str
    description: str
    available: bool


ALGORITHM_REGISTRY: dict[str, AlgorithmRegistration] = {
    "static": AlgorithmRegistration(
        "static",
        StaticRoutingAlgorithm,
        "Static shortest path",
        "Shortest path with unit cost per link (fewest hops).",
        True,
    ),
    "ecmp": AlgorithmRegistration(
        "ecmp",
        EcmpRoutingAlgorithm,
        "ECMP",
        "Deterministic selection among equal-hop shortest paths.",
        True,
    ),
    "lstm_ql": AlgorithmRegistration(
        "lstm_ql",
        None,
        "LSTM + Q-learning",
        "Requires a trained sequence model and an integrated policy.",
        False,
    ),
    "gcn_dqn": AlgorithmRegistration(
        "gcn_dqn",
        None,
        "GCN + DQN",
        "Requires an integrated graph encoder and trained DQN policy.",
        False,
    ),
    "gat_d3qn": AlgorithmRegistration(
        "gat_d3qn",
        None,
        "GAT + D3QN",
        "Requires a trained graph-attention D3QN policy.",
        False,
    ),
}


def create_routing_algorithm(name: str) -> RoutingAlgorithm:
    registration = ALGORITHM_REGISTRY.get(name)
    if registration is None:
        raise ValueError(f"Unknown routing algorithm: {name}")
    if registration.factory is None:
        raise ValueError(
            f"Routing algorithm '{name}' is registered but not available: "
            f"{registration.description}"
        )
    return registration.factory()
