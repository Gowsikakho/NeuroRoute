from __future__ import annotations

import math
import random
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from itertools import islice

import networkx as nx
import simpy

from .controller import SimulatedOpenFlowController
from .network_backend import NetworkBackend
from .queue_agent import QueueRateQLearningAgent
from .storage import save_queue_samples
from .topologies import TOPOLOGIES, TopologyDefinition

AVAILABLE_ALGORITHMS = {
    "static-dijkstra",
    "ecmp",
    "queue-q-learning",
}

ALGORITHM_CATALOG = [
    {
        "id": "proactive-d3qn",
        "name": "Proactive D3QN",
        "family": "reinforcement learning",
        "description": "Double dueling DQN routing using current and predicted candidate-path queue state.",
        "available": False,
    },
    {
        "id": "static-dijkstra",
        "name": "Static Dijkstra",
        "family": "classical",
        "description": "Shortest path selected by configured link delay.",
        "available": True,
    },
    {
        "id": "ecmp",
        "name": "ECMP",
        "family": "classical",
        "description": "Seeded selection among equal-cost shortest paths.",
        "available": True,
    },
    {
        "id": "queue-q-learning",
        "name": "Tabular Q-learning",
        "family": "reinforcement learning",
        "description": "Learns a per-tick LS/TS queue service-rate split from observed occupancy.",
        "available": True,
    },
    {
        "id": "lstm-q-learning",
        "name": "LSTM + Q-learning",
        "family": "research baseline",
        "description": "Sequence predictor and queue-aware Q-learning baseline.",
        "available": False,
    },
    {
        "id": "cheb-gcn-dqn",
        "name": "Chebyshev-GCN + DQN",
        "family": "research baseline",
        "description": "Graph-convolutional occupancy encoder with a DQN routing policy.",
        "available": False,
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulationManager(NetworkBackend):
    """Thread-safe SimPy runner with a small, reproducible queueing model."""

    def __init__(self, persist: bool = True) -> None:
        self._lock = threading.RLock()
        self._persist = persist
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._metrics: deque[dict] = deque(maxlen=14 * 3600)
        self._link_utilization: dict[str, float] = {}
        self._state = {
            "id": "idle",
            "status": "idle",
            "topologyId": "demo-7",
            "algorithm": "static-dijkstra",
            "elapsedSeconds": 0.0,
            "durationSeconds": 0,
            "activeFlows": 0,
            "averageLatencyMs": 0.0,
            "throughputMbps": 0.0,
            "packetLossPercent": 0.0,
            "updatedAt": _utc_now(),
        }
        self._topology: TopologyDefinition = TOPOLOGIES["demo-7"]
        self._queues: dict[str, dict[str, float]] = {
            f"s{index}": {"ls": 0.0, "ts": 0.0}
            for index in range(self._topology.node_count)
        }
        self._graph = nx.Graph()
        for index, (left, right) in enumerate(self._topology.edges):
            delay = self._topology.delay_ms + ((index * 7) % 18)
            self._graph.add_edge(
                f"s{left}",
                f"s{right}",
                delay=float(delay),
                weight=float(delay),
                id=f"s{left}-s{right}",
            )
        self._environment: simpy.Environment | None = None
        self._random = random.Random(0)
        self._queue_agent: QueueRateQLearningAgent | None = None
        self._ls_rate = 35.0
        self._ts_rate = 50.0
        self._lost_packets = 0
        self._generated_packets = 0
        self._latencies: list[float] = []
        self._throughput_packets = 0.0
        self._throughputs: list[float] = []
        self._service_split = 0.62
        self._controller = SimulatedOpenFlowController()
        self._controller.reset(list(self._queues))
        self._queue_capacity = 300.0
        self._service_capacity = 95.0

    def _configure(
        self,
        topology_id: str,
        algorithm: str,
        duration_seconds: int,
        seed: int,
        ls_rate: float,
        ts_rate: float,
    ) -> None:
        self._topology = TOPOLOGIES[topology_id]
        self._graph = nx.Graph()
        for index, (left, right) in enumerate(self._topology.edges):
            delay = self._topology.delay_ms + ((index * 7) % 18)
            self._graph.add_edge(
                f"s{left}",
                f"s{right}",
                delay=float(delay),
                weight=float(delay),
                id=f"s{left}-s{right}",
            )
        self._queues = {
            f"s{index}": {"ls": 0.0, "ts": 0.0}
            for index in range(self._topology.node_count)
        }
        self._controller.reset(list(self._queues))
        self._link_utilization = {}
        self._random = random.Random(seed)
        self._queue_agent = (
            QueueRateQLearningAgent(seed) if algorithm == "queue-q-learning" else None
        )
        self._ls_rate = ls_rate
        self._ts_rate = ts_rate
        self._lost_packets = 0
        self._generated_packets = 0
        self._latencies = []
        self._throughput_packets = 0.0
        self._throughputs = []
        self._service_split = 0.62
        self._metrics.clear()
        self._stop_event.clear()
        self._environment = simpy.Environment()
        self._environment.process(self._tick_process())
        self._state = {
            "id": str(uuid.uuid4()),
            "status": "running",
            "topologyId": topology_id,
            "algorithm": algorithm,
            "elapsedSeconds": 0.0,
            "durationSeconds": duration_seconds,
            "activeFlows": 0,
            "averageLatencyMs": 0.0,
            "throughputMbps": 0.0,
            "packetLossPercent": 0.0,
            "updatedAt": _utc_now(),
        }

    def start(
        self,
        topology_id: str,
        algorithm: str,
        duration_seconds: int,
        seed: int,
        ls_rate: float,
        ts_rate: float,
    ) -> dict:
        if topology_id not in TOPOLOGIES:
            raise ValueError(f"Unknown topology: {topology_id}")
        if algorithm not in AVAILABLE_ALGORITHMS:
            raise ValueError(f"Algorithm is not available: {algorithm}")
        with self._lock:
            if self._state["status"] == "running":
                raise RuntimeError("Stop the current simulation before starting another.")
            self._configure(
                topology_id, algorithm, duration_seconds, seed, ls_rate, ts_rate
            )
            if self._persist:
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="neuroroute-simpy",
                    daemon=True,
                )
                self._thread.start()
            return dict(self._state)

    def _run_loop(self) -> None:
        environment = self._environment
        if environment is None:
            return
        while not self._stop_event.is_set():
            with self._lock:
                if self._state["status"] != "running":
                    return
                if self._state["elapsedSeconds"] >= self._state["durationSeconds"]:
                    self._state["status"] = "completed"
                    self._state["updatedAt"] = _utc_now()
                    return
            environment.run(until=environment.now + 1.00001)
            if self._stop_event.wait(1.0):
                return

    def _tick_process(self):
        while True:
            yield self._environment.timeout(1)
            with self._lock:
                if self._state["status"] != "running":
                    return
                if self._state["elapsedSeconds"] >= self._state["durationSeconds"]:
                    self._state["status"] = "completed"
                    self._state["updatedAt"] = _utc_now()
                    return
                self._advance()

    def _poisson(self, mean: float) -> int:
        if mean <= 0:
            return 0
        threshold = math.exp(-mean)
        product = 1.0
        count = 0
        while product > threshold:
            count += 1
            product *= self._random.random()
        return count - 1

    def _candidate_paths(self, source: str, target: str) -> list[list[str]]:
        try:
            return list(islice(nx.shortest_simple_paths(self._graph, source, target, weight="weight"), 4))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def _choose_path(self, source: str, target: str) -> list[str]:
        algorithm = self._state["algorithm"]
        if algorithm == "ecmp":
            try:
                paths = list(nx.all_shortest_paths(self._graph, source, target, weight="weight"))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return []
            return self._random.choice(paths)
        if algorithm == "queue-q-learning":
            paths = self._candidate_paths(source, target)
            if not paths:
                return []
            return min(
                paths,
                key=lambda path: sum(
                    self._queues[node]["ls"] + self._queues[node]["ts"]
                    for node in path[1:-1]
                ),
            )
        try:
            return nx.shortest_path(self._graph, source, target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def _send_packets(
        self,
        traffic_class: str,
        count: int,
        link_load: dict[str, int],
    ) -> int:
        nodes = list(self._queues)
        accepted = 0
        for _ in range(count):
            source, target = self._random.sample(nodes, 2)
            path = self._choose_path(source, target)
            if len(path) < 2:
                self._lost_packets += 1
                continue
            self._generated_packets += 1
            queue_nodes = path[1:-1]
            if any(
                sum(self._queues[node].values()) >= self._queue_capacity
                for node in queue_nodes
            ):
                self._lost_packets += 1
                continue
            accepted += 1
            self._controller.record_path(path, traffic_class, 1)
            for node in queue_nodes:
                self._queues[node][traffic_class] += 1
            path_delay = sum(
                self._graph[left][right]["delay"]
                for left, right in zip(path, path[1:])
            )
            queue_delay = sum(
                sum(self._queues[node].values()) / self._queue_capacity * 10
                for node in queue_nodes
            )
            self._latencies.append(path_delay + queue_delay)
            for left, right in zip(path, path[1:]):
                edge_id = self._graph[left][right]["id"]
                link_load[edge_id] = link_load.get(edge_id, 0) + 1
        return accepted

    def _advance(self) -> None:
        ls_mean = self._ls_rate * 0.65
        ts_count = int(self._ts_rate * 0.65)
        ls_count = self._poisson(ls_mean)
        link_load: dict[str, int] = {}
        accepted_ls = self._send_packets("ls", ls_count, link_load)
        accepted_ts = self._send_packets("ts", ts_count, link_load)

        average_ls = sum(queue["ls"] for queue in self._queues.values()) / max(
            len(self._queues), 1
        )
        average_ts = sum(queue["ts"] for queue in self._queues.values()) / max(
            len(self._queues), 1
        )
        if self._queue_agent is not None:
            self._service_split = self._queue_agent.choose_rate(average_ls, average_ts)

        served = 0.0
        for switch_id, queue in self._queues.items():
            switch_split = self._controller.queue_rate(
                switch_id, self._service_split
            )
            ls_service = self._service_capacity * switch_split
            ts_service = self._service_capacity - ls_service
            ls_served = min(queue["ls"], ls_service)
            ts_served = min(queue["ts"], ts_service)
            queue["ls"] -= ls_served
            queue["ts"] -= ts_served
            served += ls_served + ts_served

        for link in self._topology.edges:
            left, right = link
            link_id = f"s{left}-s{right}"
            reverse_id = f"s{right}-s{left}"
            load = link_load.get(link_id, link_load.get(reverse_id, 0))
            self._link_utilization[link_id] = min(100.0, load * 1.5)

        self._throughput_packets = served
        self._throughputs.append(served * 1200 * 8 / 1_000_000)
        elapsed = float(self._state["elapsedSeconds"] + 1)
        average_latency = (
            sum(self._latencies) / len(self._latencies) if self._latencies else 0.0
        )
        loss = (
            100 * self._lost_packets / self._generated_packets
            if self._generated_packets
            else 0.0
        )
        points = []
        timestamp = time.time()
        for switch_id, queue in self._queues.items():
            point = {
                "timestamp": timestamp,
                "elapsedSeconds": elapsed,
                "switchId": switch_id,
                "lsOccupancy": round(queue["ls"] / self._queue_capacity * 100, 3),
                "tsOccupancy": round(queue["ts"] / self._queue_capacity * 100, 3),
                "capacity": 100.0,
            }
            self._metrics.append(point)
            points.append(point)

        self._state.update(
            {
                "elapsedSeconds": elapsed,
                "activeFlows": int(accepted_ls + accepted_ts),
                "averageLatencyMs": round(average_latency, 3),
                "throughputMbps": round(served * 1200 * 8 / 1_000_000, 3),
                "packetLossPercent": round(loss, 3),
                "updatedAt": _utc_now(),
            }
        )
        if self._persist:
            save_queue_samples(points)

    def stop(self) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                self._state["status"] = "stopped"
                self._state["updatedAt"] = _utc_now()
                self._stop_event.set()
            return dict(self._state)

    def reset(self) -> dict:
        self.stop()
        with self._lock:
            self._metrics.clear()
            self._link_utilization = {}
            self._state = {
                "id": "idle",
                "status": "idle",
                "topologyId": "demo-7",
                "algorithm": "static-dijkstra",
                "elapsedSeconds": 0.0,
                "durationSeconds": 0,
                "activeFlows": 0,
                "averageLatencyMs": 0.0,
                "throughputMbps": 0.0,
                "packetLossPercent": 0.0,
                "updatedAt": _utc_now(),
            }
            return dict(self._state)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def topologies(self) -> list[dict]:
        with self._lock:
            return [
                topology.as_dict(
                    self._link_utilization
                    if topology_id == self._state["topologyId"]
                    else None
                )
                for topology_id, topology in TOPOLOGIES.items()
            ]

    def discover_topology(self) -> list[dict]:
        return self.topologies()

    def get_port_stats(self) -> list[dict]:
        return self.controller_state()["ports"]

    def get_queue_stats(self, window_seconds: int = 120) -> list[dict]:
        return self.metrics(window_seconds)

    def metrics(self, window_seconds: int = 120) -> list[dict]:
        with self._lock:
            cutoff = max(0.0, self._state["elapsedSeconds"] - window_seconds)
            return [
                dict(point)
                for point in self._metrics
                if point["elapsedSeconds"] >= cutoff
            ]

    def controller_state(self) -> dict:
        with self._lock:
            return self._controller.state(list(self._metrics))

    def install_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        with self._lock:
            return self._controller.install_queue_rate(switch_id, ls_share)

    def install_flow_rule(self, switch_id: str, rule: dict) -> dict:
        with self._lock:
            return self._controller.install_flow_rule(switch_id, rule)

    @classmethod
    def run_offline(
        cls,
        topology_id: str,
        algorithm: str,
        duration_seconds: int,
        seed: int,
    ) -> dict:
        runner = cls(persist=False)
        runner._configure(topology_id, algorithm, duration_seconds, seed, 35, 50)
        environment = runner._environment
        for _ in range(duration_seconds):
            environment.run(until=environment.now + 1.00001)
        samples = list(runner._metrics)
        queue_occupancy = [
            point["lsOccupancy"] + point["tsOccupancy"] for point in samples
        ]
        state = runner.snapshot()
        return {
            "algorithm": algorithm,
            "meanLatencyMs": state["averageLatencyMs"],
            "throughputMbps": round(
                sum(runner._throughputs) / len(runner._throughputs), 3
            )
            if runner._throughputs
            else 0.0,
            "packetLossPercent": state["packetLossPercent"],
            "meanQueueOccupancy": round(
                sum(queue_occupancy) / len(queue_occupancy), 3
            )
            if queue_occupancy
            else 0.0,
        }


simulation = SimulationManager()
