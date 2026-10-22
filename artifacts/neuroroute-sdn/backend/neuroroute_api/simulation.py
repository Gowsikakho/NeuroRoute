from __future__ import annotations

import hashlib
import math
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
from typing import Iterator

import networkx as nx
import simpy

from .controller import SimulatedOpenFlowController
from .network_backend import NetworkBackend
from .queue_agent import QueueRateQLearningAgent
from .queue_model import (
    QUEUE_CAPACITY_BYTES,
    OutputPortQueue,
    Packet,
)
from .routing import (
    ALGORITHM_REGISTRY,
    RoutingAlgorithm,
    create_routing_algorithm,
)
from .storage import save_queue_samples
from .topologies import TOPOLOGIES, TopologyDefinition

LEGACY_AVAILABLE_ALGORITHMS = {
    "static-dijkstra",
    "ecmp",
    "queue-q-learning",
    "proactive-threshold",
}
AVAILABLE_ALGORITHMS = LEGACY_AVAILABLE_ALGORITHMS | {
    name for name, registration in ALGORITHM_REGISTRY.items() if registration.available
}

_ROUTING_ALIASES = {"static-dijkstra": "static"}

ALGORITHM_CATALOG = [
    {
        "id": "proactive-d3qn",
        "name": "Proactive D3QN",
        "family": "reinforcement learning",
        "description": "Double dueling DQN routing using current and predicted candidate-path queue state.",
        "available": False,
    },
    {
        "id": "proactive-threshold",
        "name": "Proactive queue-threshold agent",
        "family": "proactive baseline",
        "description": "Reroutes when a two-step occupancy projection reaches the congestion threshold.",
        "available": True,
    },
    {
        "id": "static-dijkstra",
        "name": "Static Dijkstra",
        "family": "classical",
        "description": "Shortest path by hop count; congestion reroutes are reactive.",
        "available": True,
    },
    {
        "id": "ecmp",
        "name": "ECMP",
        "family": "classical",
        "description": "Flow-hash selection among equal-hop shortest paths; congestion reroutes are reactive.",
        "available": True,
    },
    {
        "id": "queue-q-learning",
        "name": "Tabular Q-learning",
        "family": "reinforcement learning",
        "description": "Learns an LS/TS token-bucket rate split from observed queue occupancy.",
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
        "description": "Graph-convolutional occupancy encoder with a DQN policy.",
        "available": False,
    },
]
ALGORITHM_CATALOG.extend(
    {
        "id": registration.name,
        "name": registration.display_name,
        "family": "classical" if registration.name in {"static", "ecmp"} else "research baseline",
        "description": registration.description,
        "available": registration.available,
    }
    for registration in ALGORITHM_REGISTRY.values()
    if registration.name not in {algorithm["id"] for algorithm in ALGORITHM_CATALOG}
)

FLOW_COUNT_PER_CLASS = 4
LS_PACKET_BYTES = 200
TS_PACKET_BYTES = 1500
LS_MAX_LINK_SHARE = 0.05
LS_ON_MEAN_SECONDS = 1.5
LS_OFF_MEAN_SECONDS = 1.5
CONGESTION_THRESHOLD_PERCENT = 80.0
PROACTIVE_LEAD_STEPS = 2
RULE_INSTALLATION_MIN_SECONDS = 0.05
RULE_INSTALLATION_MAX_SECONDS = 0.2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flow_seed(seed: int, flow_id: str) -> int:
    digest = hashlib.sha256(f"{seed}:{flow_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass
class FlowState:
    flow_id: str
    traffic_class: str
    source: str
    destination: str
    packet_bytes: int
    arrival_rate_pps: float
    seed: int
    random_source: random.Random
    path: list[str] = field(default_factory=list)
    generated_packets: int = 0
    delivered_packets: int = 0
    dropped_packets: int = 0
    latency_sum_ms: float = 0.0
    reroutes: int = 0
    rerouting: bool = False
    done: bool = False
    last_reroute_at: float = -math.inf
    last_queue_levels: dict[str, float] = field(default_factory=dict)


class SimulationManager(NetworkBackend):
    """Thread-safe SimPy SDN simulator with seeded per-flow traffic streams."""

    def __init__(self, persist: bool = True) -> None:
        self._lock = threading.RLock()
        self._persist = persist
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._metrics: deque[dict] = deque(maxlen=14 * 3600)
        self._last_poll_elapsed = 0.0
        self._topology: TopologyDefinition = TOPOLOGIES["demo-7"]
        self._graph = nx.Graph()
        self._edge_attributes: dict[str, dict] = {}
        self._link_status: dict[str, bool] = {}
        self._output_ports: dict[str, OutputPortQueue] = {}
        self._queues: dict[str, dict[str, float]] = {}
        self._flows: list[FlowState] = []
        self._flows_by_id: dict[str, FlowState] = {}
        self._link_utilization: dict[str, float] = {}
        self._link_events: list[dict] = []
        self._environment: simpy.Environment | None = None
        self._queue_agent: QueueRateQLearningAgent | None = None
        self._selected_mode = "static"
        self._routing_algorithm: RoutingAlgorithm | None = create_routing_algorithm("static")
        self._control_loop_ticks = 0
        self._last_control_reward = 0.0
        self._agent_updates = 0
        self._controller = SimulatedOpenFlowController()
        self._random = random.Random(0)
        self._service_split = 0.62
        self._ls_rate = 50.0
        self._ts_rate = 50.0
        self._speed_mode = "realtime"
        self._duration_seconds = 0
        self._lost_packets = 0
        self._delivered_packets = 0
        self._generated_packets = 0
        self._interval_delivered_bytes = 0
        self._throughputs: list[float] = []
        self._reroute_count = 0
        self._build_network(self._topology, None)
        self._controller.reset(list(self._queues))
        self._state = self._idle_state()

    def _idle_state(self) -> dict:
        return {
            "id": "idle",
            "status": "idle",
            "topologyId": "demo-7",
            "algorithm": self._selected_mode,
            "elapsedSeconds": 0.0,
            "durationSeconds": 0,
            "activeFlows": 0,
            "averageLatencyMs": 0.0,
            "throughputMbps": 0.0,
            "packetLossPercent": 0.0,
            "droppedPackets": 0,
            "transmitRateMbps": 0.0,
            "speedMode": "realtime",
            "flowMetrics": [],
            "linkEvents": [],
            "rerouteCount": 0,
            "updatedAt": _utc_now(),
        }

    def _build_network(
        self,
        topology: TopologyDefinition,
        environment: simpy.Environment | None,
    ) -> None:
        self._topology = topology
        self._graph = nx.Graph()
        self._graph.add_nodes_from(f"s{index}" for index in range(topology.node_count))
        self._edge_attributes = {}
        self._link_status = {}
        self._output_ports = {}
        self._link_utilization = {}
        for index, (left_index, right_index) in enumerate(topology.edges):
            left, right = f"s{left_index}", f"s{right_index}"
            link_id = f"{left}-{right}"
            delay = float(topology.delay_ms + ((index * 7) % 18))
            attributes = {
                "delay": delay,
                "weight": 1.0,
                "id": link_id,
            }
            self._edge_attributes[link_id] = attributes
            self._link_status[link_id] = True
            self._link_utilization[link_id] = 0.0
            self._graph.add_edge(left, right, **attributes)
            for source, target in ((left, right), (right, left)):
                port = OutputPortQueue(
                    source=source,
                    target=target,
                    bandwidth_mbps=topology.bandwidth_mbps,
                )
                self._output_ports[port.port_id] = port

        self._queues = {
            f"s{index}": {"ls": 0.0, "ts": 0.0}
            for index in range(topology.node_count)
        }
        if environment is not None:
            for port in self._output_ports.values():
                environment.process(
                    port.service_process(
                        environment,
                        self._service_rate_share,
                        self._on_port_transmit,
                    )
                )

    def _configure(
        self,
        topology_id: str,
        algorithm: str,
        duration_seconds: int,
        seed: int,
        ls_rate: float,
        ts_rate: float,
        speed_mode: str = "fast",
        failure_schedule: dict | None = None,
    ) -> None:
        self._stop_event.clear()
        self._duration_seconds = duration_seconds
        self._speed_mode = speed_mode
        self._ls_rate = ls_rate
        self._ts_rate = ts_rate
        self._random = random.Random(seed)
        self._queue_agent = (
            QueueRateQLearningAgent(seed)
            if algorithm == "queue-q-learning"
            else None
        )
        routing_name = _ROUTING_ALIASES.get(algorithm, algorithm)
        self._routing_algorithm = (
            create_routing_algorithm(routing_name)
            if routing_name in {"static", "ecmp"}
            else None
        )
        self._selected_mode = routing_name if routing_name in ALGORITHM_REGISTRY else "static"
        self._control_loop_ticks = 0
        self._last_control_reward = 0.0
        self._agent_updates = 0
        self._service_split = 0.62
        self._lost_packets = 0
        self._delivered_packets = 0
        self._generated_packets = 0
        self._interval_delivered_bytes = 0
        self._throughputs = []
        self._reroute_count = 0
        self._metrics.clear()
        self._last_poll_elapsed = 0.0
        self._link_events = []
        self._flows = []
        self._flows_by_id = {}
        self._environment = simpy.Environment()
        self._controller.reset(
            [f"s{index}" for index in range(TOPOLOGIES[topology_id].node_count)]
        )
        self._build_network(TOPOLOGIES[topology_id], self._environment)

        self._state = {
            "id": str(uuid.uuid4()),
            "status": "running",
            "topologyId": topology_id,
            "algorithm": algorithm,
            "elapsedSeconds": 0.0,
            "durationSeconds": duration_seconds,
            "activeFlows": FLOW_COUNT_PER_CLASS * 2,
            "averageLatencyMs": 0.0,
            "throughputMbps": 0.0,
            "packetLossPercent": 0.0,
            "droppedPackets": 0,
            "transmitRateMbps": 0.0,
            "speedMode": speed_mode,
            "flowMetrics": [],
            "linkEvents": [],
            "rerouteCount": 0,
            "updatedAt": _utc_now(),
        }
        self._flows = self._create_flows(seed)
        self._flows_by_id = {flow.flow_id: flow for flow in self._flows}
        for flow in self._flows:
            flow.path = self._find_route(flow)
            if flow.path:
                self._controller.record_path(flow.path, flow.traffic_class, 0)
            self._environment.process(self._flow_process(flow))

        self._environment.process(self._stats_poll_process())
        if failure_schedule:
            self._environment.process(self._failure_process(failure_schedule))

        self._state["activeFlows"] = len(self._flows)

    def _create_flows(self, seed: int) -> list[FlowState]:
        nodes = list(self._graph.nodes)
        total_link_bps = self._topology.bandwidth_mbps * 1_000_000
        ls_average_bps = total_link_bps * LS_MAX_LINK_SHARE * self._ls_rate / 100
        ts_total_bps = total_link_bps * self._ts_rate / 100
        flows: list[FlowState] = []
        for traffic_class, packet_bytes, total_bps in (
            ("ls", LS_PACKET_BYTES, ls_average_bps),
            ("ts", TS_PACKET_BYTES, ts_total_bps),
        ):
            per_flow_bps = total_bps / FLOW_COUNT_PER_CLASS
            if traffic_class == "ls":
                # LS is active half the time; double its active-burst rate.
                per_flow_bps *= 2
            rate_pps = per_flow_bps / (packet_bytes * 8)
            for index in range(FLOW_COUNT_PER_CLASS):
                flow_id = f"{traffic_class}-{index + 1}"
                flow_seed = _flow_seed(seed, flow_id)
                random_source = random.Random(flow_seed)
                source, destination = random_source.sample(nodes, 2)
                flows.append(
                    FlowState(
                        flow_id=flow_id,
                        traffic_class=traffic_class,
                        source=source,
                        destination=destination,
                        packet_bytes=packet_bytes,
                        arrival_rate_pps=rate_pps,
                        seed=flow_seed,
                        random_source=random_source,
                    )
                )
        return flows

    def _flow_process(self, flow: FlowState) -> Iterator[simpy.Event]:
        environment = self._environment
        if environment is None:
            return
        duration = float(self._duration_seconds)
        if flow.arrival_rate_pps <= 0:
            yield environment.timeout(duration)
            flow.done = True
            return

        if flow.traffic_class == "ls":
            while environment.now < duration:
                on_duration = flow.random_source.expovariate(1 / LS_ON_MEAN_SECONDS)
                burst_end = min(duration, environment.now + on_duration)
                while environment.now < burst_end:
                    interval = flow.random_source.expovariate(
                        flow.arrival_rate_pps
                    )
                    if environment.now + interval >= burst_end:
                        yield environment.timeout(burst_end - environment.now)
                        break
                    yield environment.timeout(interval)
                    self._generate_packet(flow)
                if environment.now < duration:
                    off_duration = flow.random_source.expovariate(
                        1 / LS_OFF_MEAN_SECONDS
                    )
                    yield environment.timeout(min(off_duration, duration - environment.now))
        else:
            interval = 1 / flow.arrival_rate_pps
            yield environment.timeout(flow.random_source.random() * interval)
            while environment.now < duration:
                self._generate_packet(flow)
                yield environment.timeout(interval)
        flow.done = True

    def _generate_packet(self, flow: FlowState) -> None:
        environment = self._environment
        if environment is None:
            return
        flow.generated_packets += 1
        self._generated_packets += 1
        self._controller.record_path(flow.path, flow.traffic_class, 1)
        if len(flow.path) < 2:
            self._drop_flow_packet(flow)
            return
        packet = Packet(
            flow_id=flow.flow_id,
            traffic_class=flow.traffic_class,
            size_bytes=flow.packet_bytes,
            created_at=float(environment.now),
            path=list(flow.path),
        )
        self._enqueue_packet(packet)

    def _enqueue_packet(self, packet: Packet) -> None:
        if packet.hop_index >= len(packet.path) - 1:
            self._deliver_packet(packet)
            return
        source = packet.path[packet.hop_index]
        target = packet.path[packet.hop_index + 1]
        port = self._output_ports.get(f"{source}:{target}")
        if port is None or not port.enqueue(packet):
            self._drop_packet(packet)

    def _on_port_transmit(self, packet: Packet, port: OutputPortQueue) -> None:
        self._controller.record_port_transmission(port.source, port.target)
        packet.hop_index += 1
        if packet.hop_index >= len(packet.path) - 1:
            self._deliver_packet(packet)
        else:
            self._enqueue_packet(packet)

    def _deliver_packet(self, packet: Packet) -> None:
        environment = self._environment
        if environment is None:
            return
        flow = self._flows_by_id[packet.flow_id]
        flow.delivered_packets += 1
        flow.latency_sum_ms += max(0.0, float(environment.now) - packet.created_at) * 1000
        self._delivered_packets += 1
        self._interval_delivered_bytes += packet.size_bytes

    def _drop_flow_packet(self, flow: FlowState) -> None:
        flow.dropped_packets += 1
        self._lost_packets += 1

    def _drop_packet(self, packet: Packet) -> None:
        self._drop_flow_packet(self._flows_by_id[packet.flow_id])

    def _service_rate_share(self, switch_id: str) -> float:
        return self._controller.queue_rate(switch_id, self._service_split)

    def _find_route(
        self,
        flow: FlowState,
        avoid_ports: set[str] | None = None,
    ) -> list[str]:
        graph = self._graph
        if avoid_ports:
            graph = self._graph.copy()
            for port_id in avoid_ports:
                source, target = port_id.split(":", 1)
                if graph.has_edge(source, target):
                    graph.remove_edge(source, target)
        algorithm = self._routing_algorithm or create_routing_algorithm("static")
        return algorithm.select_path(
            graph,
            flow.flow_id,
            flow.source,
            flow.destination,
        )

    def _failure_process(self, schedule: dict) -> Iterator[simpy.Event]:
        environment = self._environment
        if environment is None:
            return
        down_at = float(schedule["downSeconds"])
        up_at = float(schedule["upSeconds"])
        yield environment.timeout(down_at)
        self._set_link_state(schedule["linkId"], False)
        yield environment.timeout(up_at - down_at)
        self._set_link_state(schedule["linkId"], True)

    def _set_link_state(self, link_id: str, is_up: bool) -> None:
        environment = self._environment
        if environment is None or self._link_status.get(link_id) == is_up:
            return
        left, right = link_id.split("-", 1)
        if is_up:
            self._graph.add_edge(left, right, **self._edge_attributes[link_id])
        elif self._graph.has_edge(left, right):
            self._graph.remove_edge(left, right)

        pending_packets: list[Packet] = []
        for source, target in ((left, right), (right, left)):
            port = self._output_ports[f"{source}:{target}"]
            pending_packets.extend(port.set_link_state(is_up))
        for packet in pending_packets:
            self._drop_packet(packet)

        self._link_status[link_id] = is_up
        self._link_utilization[link_id] = 0.0
        event = {
            "linkId": link_id,
            "status": "up" if is_up else "down",
            "elapsedSeconds": round(float(environment.now), 3),
        }
        self._link_events.append(event)

        for flow in self._flows:
            uses_link = any(
                {source, target} == {left, right}
                for source, target in zip(flow.path, flow.path[1:])
            )
            if not flow.done and (is_up or uses_link):
                self._schedule_reroute(flow)

    def _schedule_reroute(
        self,
        flow: FlowState,
        avoid_ports: set[str] | None = None,
    ) -> None:
        environment = self._environment
        if environment is None or flow.done or flow.rerouting:
            return
        flow.rerouting = True
        delay = flow.random_source.uniform(
            RULE_INSTALLATION_MIN_SECONDS,
            RULE_INSTALLATION_MAX_SECONDS,
        )
        environment.process(self._install_route_after_delay(flow, delay, avoid_ports or set()))

    def _install_route_after_delay(
        self,
        flow: FlowState,
        delay: float,
        avoid_ports: set[str],
    ) -> Iterator[simpy.Event]:
        environment = self._environment
        if environment is None:
            return
        yield environment.timeout(delay)
        route = self._find_route(flow, avoid_ports)
        if route != flow.path:
            flow.path = route
            flow.reroutes += 1
            self._reroute_count += 1
            flow.last_queue_levels.clear()
            if route:
                self._controller.record_path(route, flow.traffic_class, 0)
        flow.last_reroute_at = float(environment.now)
        flow.rerouting = False

    def _stats_poll_process(self) -> Iterator[simpy.Event]:
        environment = self._environment
        if environment is None:
            return
        while True:
            yield environment.timeout(1)
            with self._lock:
                if self._state.get("status") != "running":
                    return
                self._poll_stats()
                self._run_routing_control_loop()
                self._check_congestion_reroutes()

    def _check_congestion_reroutes(self) -> None:
        environment = self._environment
        if environment is None:
            return
        proactive = self._state.get("algorithm") == "proactive-threshold"
        for flow in self._flows:
            if (
                flow.done
                or flow.rerouting
                or environment.now - flow.last_reroute_at < 1
            ):
                continue
            levels: dict[str, float] = {}
            avoid_ports: set[str] = set()
            for source, target in zip(flow.path, flow.path[1:]):
                port_id = f"{source}:{target}"
                port = self._output_ports.get(port_id)
                if port is None:
                    continue
                level = max(
                    port.occupancy_percent("ls"),
                    port.occupancy_percent("ts"),
                )
                previous = flow.last_queue_levels.get(port_id, level)
                projected = level + PROACTIVE_LEAD_STEPS * max(0.0, level - previous)
                trigger_level = projected if proactive else level
                if trigger_level >= CONGESTION_THRESHOLD_PERCENT:
                    avoid_ports.add(port_id)
                levels[port_id] = level
            flow.last_queue_levels = levels
            if avoid_ports:
                self._schedule_reroute(flow, avoid_ports)

    def _mean_queue_percent(self, traffic_class: str) -> float:
        if not self._output_ports:
            return 0.0
        return sum(
            port.occupancy_percent(traffic_class)
            for port in self._output_ports.values()
        ) / len(self._output_ports)

    def _poll_stats(self) -> None:
        environment = self._environment
        if environment is None:
            return
        elapsed = float(environment.now)
        timestamp = time.time()
        interval_seconds = max(elapsed - self._last_poll_elapsed, 1e-9)
        points = [
            port.sample_and_reset(interval_seconds, timestamp, elapsed)
            for port in self._output_ports.values()
        ]
        self._last_poll_elapsed = elapsed
        self._metrics.extend(points)
        for switch_queue in self._queues.values():
            switch_queue["ls"] = 0.0
            switch_queue["ts"] = 0.0
        switch_port_counts = {switch_id: 0 for switch_id in self._queues}
        for port, point in zip(self._output_ports.values(), points):
            self._queues[port.source]["ls"] += point["lsOccupancy"]
            self._queues[port.source]["ts"] += point["tsOccupancy"]
            switch_port_counts[port.source] += 1
        for switch_id, count in switch_port_counts.items():
            if count:
                self._queues[switch_id]["ls"] /= count
                self._queues[switch_id]["ts"] /= count

        if self._queue_agent is not None:
            self._service_split = self._queue_agent.choose_rate(
                self._mean_queue_percent("ls"),
                self._mean_queue_percent("ts"),
            )
        total_tx_mbps = sum(point["transmitRateMbps"] for point in points)
        for link_id in self._link_status:
            left, right = link_id.split("-", 1)
            direction_rates = [
                point["transmitRateMbps"]
                for port, point in zip(self._output_ports.values(), points)
                if (port.source, port.target) in ((left, right), (right, left))
            ]
            self._link_utilization[link_id] = (
                min(
                    100.0,
                    max(direction_rates, default=0.0)
                    / max(self._topology.bandwidth_mbps, 1e-9)
                    * 100,
                )
                if self._link_status[link_id]
                else 0.0
            )
        if self._persist:
            save_queue_samples(points)

        mean_latency = (
            sum(flow.latency_sum_ms for flow in self._flows)
            / self._delivered_packets
            if self._delivered_packets
            else 0.0
        )
        loss_percent = (
            self._lost_packets * 100 / self._generated_packets
            if self._generated_packets
            else 0.0
        )
        throughput_mbps = self._interval_delivered_bytes * 8 / 1_000_000
        self._throughputs.append(throughput_mbps)
        self._interval_delivered_bytes = 0
        self._state.update(
            {
                "elapsedSeconds": elapsed,
                "activeFlows": sum(not flow.done for flow in self._flows),
                "averageLatencyMs": round(mean_latency, 3),
                "throughputMbps": round(throughput_mbps, 6),
                "packetLossPercent": round(loss_percent, 4),
                "droppedPackets": self._lost_packets,
                "transmitRateMbps": round(total_tx_mbps, 6),
                "flowMetrics": self._flow_metrics(),
                "linkEvents": [dict(event) for event in self._link_events],
                "rerouteCount": self._reroute_count,
                "updatedAt": _utc_now(),
            }
        )
        if self._queue_agent is not None and self._routing_algorithm is None:
            reward = (
                float(self._state.get("throughputMbps", 0.0))
                - float(self._state.get("averageLatencyMs", 0.0)) * 0.01
                - float(self._state.get("packetLossPercent", 0.0)) * 0.1
            )
            self._queue_agent.observe_reward(reward)
            self._queue_agent.update()
            self._agent_updates += 1

    def _run_routing_control_loop(self) -> None:
        """Collect, predict, decide, install, observe reward, and update agents."""
        algorithm = self._routing_algorithm
        if algorithm is None:
            return

        topology = self.get_topology()
        stats = self.poll_stats()
        state = {
            "topology": topology,
            "stats": stats,
            "flows": [
                {
                    "flowId": flow.flow_id,
                    "source": flow.source,
                    "destination": flow.destination,
                    "trafficClass": flow.traffic_class,
                    "active": not flow.done,
                }
                for flow in self._flows
                if not flow.done
            ],
        }
        state["predictedQueueOccupancy"] = self._predict_queue_occupancy(stats)
        decisions = algorithm.decide(state)

        rates_by_switch: dict[str, list[float]] = {}
        for flow_id, decision in decisions.items():
            flow = self._flows_by_id.get(flow_id)
            if flow is None or flow.done:
                continue
            if decision.path and decision.path != flow.path:
                self.install_path(flow_id, decision.path)
            for switch_id in decision.path[:-1]:
                rates_by_switch.setdefault(switch_id, []).append(decision.queue_rate)

        for switch_id, rates in rates_by_switch.items():
            self.set_queue_rate(switch_id, sum(rates) / len(rates))

        self._last_control_reward = (
            float(self._state.get("throughputMbps", 0.0))
            - float(self._state.get("averageLatencyMs", 0.0)) * 0.01
            - float(self._state.get("packetLossPercent", 0.0)) * 0.1
        )
        self._observe_reward(self._last_control_reward)
        self._update_agents()
        self._control_loop_ticks += 1

    @staticmethod
    def _predict_queue_occupancy(stats: list[dict]) -> dict[str, dict[str, float]]:
        """Project each directed queue one polling interval using its recent trend."""
        by_port: dict[str, list[dict]] = {}
        for point in stats:
            by_port.setdefault(point["portId"], []).append(point)
        forecast = {}
        for port_id, points in by_port.items():
            points.sort(key=lambda point: point["elapsedSeconds"])
            current = points[-1]
            previous = points[-2] if len(points) > 1 else current
            forecast[port_id] = {
                traffic_class: min(
                    100.0,
                    max(
                        0.0,
                        float(current[f"{traffic_class}Occupancy"])
                        + max(
                            0.0,
                            float(current[f"{traffic_class}Occupancy"])
                            - float(previous[f"{traffic_class}Occupancy"]),
                        ),
                    ),
                )
                for traffic_class in ("ls", "ts")
            }
        return forecast

    def _observe_reward(self, reward: float) -> None:
        if self._queue_agent is not None:
            self._queue_agent.observe_reward(reward)

    def _update_agents(self) -> None:
        if self._queue_agent is not None:
            self._queue_agent.update()
            self._agent_updates += 1

    def _flow_metrics(self) -> list[dict]:
        result = []
        for flow in self._flows:
            loss = (
                flow.dropped_packets * 100 / flow.generated_packets
                if flow.generated_packets
                else 0.0
            )
            latency = (
                flow.latency_sum_ms / flow.delivered_packets
                if flow.delivered_packets
                else 0.0
            )
            result.append(
                {
                    "flowId": flow.flow_id,
                    "trafficClass": flow.traffic_class,
                    "source": flow.source,
                    "destination": flow.destination,
                    "generatedPackets": flow.generated_packets,
                    "deliveredPackets": flow.delivered_packets,
                    "droppedPackets": flow.dropped_packets,
                    "meanLatencyMs": round(latency, 3),
                    "lossPercent": round(loss, 4),
                    "reroutes": flow.reroutes,
                }
            )
        return result

    def start(
        self,
        topology_id: str,
        algorithm: str,
        duration_seconds: int,
        seed: int,
        ls_rate: float,
        ts_rate: float,
        speed_mode: str = "realtime",
        failure_schedule: dict | None = None,
    ) -> dict:
        if topology_id not in TOPOLOGIES:
            raise ValueError(f"Unknown topology: {topology_id}")
        if algorithm not in AVAILABLE_ALGORITHMS:
            raise ValueError(f"Algorithm is not available: {algorithm}")
        if speed_mode not in {"fast", "realtime"}:
            raise ValueError("speed_mode must be 'fast' or 'realtime'.")
        if failure_schedule:
            link_ids = {
                f"s{left}-s{right}"
                for left, right in TOPOLOGIES[topology_id].edges
            }
            if failure_schedule.get("linkId") not in link_ids:
                raise ValueError("Failure schedule refers to an unknown topology link.")
            if (
                not 0 <= failure_schedule["downSeconds"] < failure_schedule["upSeconds"]
                <= duration_seconds
            ):
                raise ValueError(
                    "Failure down/up times must be ordered and within the run duration."
                )

        with self._lock:
            if self._state["status"] == "running":
                raise RuntimeError("Stop the current simulation before starting another.")
            self._configure(
                topology_id,
                algorithm,
                duration_seconds,
                seed,
                ls_rate,
                ts_rate,
                speed_mode,
                failure_schedule,
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
        if self._environment is None:
            return
        while not self._stop_event.is_set():
            with self._lock:
                if self._state["status"] != "running":
                    return
                if self._state["elapsedSeconds"] >= self._duration_seconds:
                    self._state["status"] = "completed"
                    self._state["activeFlows"] = 0
                    self._state["updatedAt"] = _utc_now()
                    return
                self.step()
            if self._speed_mode == "realtime":
                if self._stop_event.wait(0.1):
                    return
            else:
                time.sleep(0)

    def stop(self) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                self._state["status"] = "stopped"
                self._state["updatedAt"] = _utc_now()
                self._stop_event.set()
            return self.snapshot()

    def reset(self) -> dict:
        self.stop()
        with self._lock:
            self._metrics.clear()
            self._flows = []
            self._flows_by_id = {}
            self._link_events = []
            self._environment = None
            self._build_network(TOPOLOGIES["demo-7"], None)
            self._controller.reset(list(self._queues))
            self._queue_agent = None
            self._state = self._idle_state()
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                **self._state,
                "flowMetrics": [dict(flow) for flow in self._state["flowMetrics"]],
                "linkEvents": [dict(event) for event in self._state["linkEvents"]],
            }

    def topologies(self) -> list[dict]:
        with self._lock:
            return [
                topology.as_dict(
                    self._link_utilization
                    if topology_id == self._state["topologyId"]
                    else None,
                    self._link_status if topology_id == self._state["topologyId"] else None,
                )
                for topology_id, topology in TOPOLOGIES.items()
            ]

    def get_topology(self) -> dict:
        with self._lock:
            topology_id = self._state["topologyId"]
            return TOPOLOGIES[topology_id].as_dict(
                self._link_utilization,
                self._link_status,
            )

    def poll_stats(self, window_seconds: int = 120) -> list[dict]:
        return self.metrics(window_seconds)

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
            return self._controller.state(list(self._metrics), self._service_split)

    def install_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        with self._lock:
            return self._controller.install_queue_rate(switch_id, ls_share)

    def set_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        return self.install_queue_rate(switch_id, ls_share)

    def install_path(self, flow_id: str, path: list[str]) -> dict:
        with self._lock:
            flow = self._flows_by_id.get(flow_id)
            if flow is None:
                raise ValueError(f"Unknown flow: {flow_id}")
            if (
                not path
                or path[0] != flow.source
                or path[-1] != flow.destination
                or any(
                    not self._graph.has_edge(source, target)
                    for source, target in zip(path, path[1:])
                )
            ):
                raise ValueError(f"Invalid or unavailable path for flow: {flow_id}")
            flow.path = list(path)
            flow.last_queue_levels.clear()
            self._controller.record_path(path, flow.traffic_class, 0)
            return {"flowId": flow_id, "path": list(path)}

    def start_traffic(self, config: dict) -> dict:
        def value(camel: str, snake: str, default: object = None) -> object:
            return config.get(camel, config.get(snake, default))

        topology_id = value("topologyId", "topology_id")
        duration = value("durationSeconds", "duration_seconds")
        seed = config.get("seed")
        if topology_id is None or duration is None or seed is None:
            raise ValueError("Traffic config requires topologyId, durationSeconds, and seed.")
        return self.start(
            str(topology_id),
            str(config.get("algorithm", self._selected_mode)),
            int(duration),
            int(seed),
            float(value("lsRate", "ls_rate", 35)),
            float(value("tsRate", "ts_rate", 50)),
            str(value("speedMode", "speed_mode", "realtime")),
            value("failureSchedule", "failure_schedule"),
        )

    def stop_traffic(self) -> dict:
        return self.stop()

    def fail_link(self, link_id: str) -> dict:
        with self._lock:
            if self._environment is None or self._state["status"] != "running":
                raise RuntimeError("A running simulation is required to fail a link.")
            if link_id not in self._link_status:
                raise ValueError(f"Unknown link: {link_id}")
            self._set_link_state(link_id, False)
            return {"linkId": link_id, "status": "down"}

    def restore_link(self, link_id: str) -> dict:
        with self._lock:
            if self._environment is None or self._state["status"] != "running":
                raise RuntimeError("A running simulation is required to restore a link.")
            if link_id not in self._link_status:
                raise ValueError(f"Unknown link: {link_id}")
            self._set_link_state(link_id, True)
            return {"linkId": link_id, "status": "up"}

    def step(self) -> dict:
        with self._lock:
            environment = self._environment
            if environment is None:
                raise RuntimeError("No simulation is configured.")
            if self._state["status"] != "running":
                raise RuntimeError("A running simulation is required to step.")
            target = min(
                float(environment.now) + 0.1,
                self._duration_seconds + 0.00001,
            )
            environment.run(until=target)
            return self.snapshot()

    def set_mode(self, mode: str) -> dict:
        with self._lock:
            algorithm = create_routing_algorithm(mode)
            self._routing_algorithm = algorithm
            self._queue_agent = None
            self._selected_mode = mode
            self._state["algorithm"] = mode
            self._state["updatedAt"] = _utc_now()
            return {"mode": self._selected_mode, "simulation": self.snapshot()}

    def mode(self) -> dict:
        with self._lock:
            return {
                "mode": self._selected_mode,
                "algorithms": [
                    {
                        "name": registration.name,
                        "displayName": registration.display_name,
                        "available": registration.available,
                        "description": registration.description,
                    }
                    for registration in ALGORITHM_REGISTRY.values()
                ],
            }

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
        ls_rate: float = 35,
        ts_rate: float = 50,
    ) -> dict:
        runner = cls(persist=False)
        runner._configure(
            topology_id,
            algorithm,
            duration_seconds,
            seed,
            ls_rate,
            ts_rate,
            speed_mode="fast",
        )
        environment = runner._environment
        if environment is None:
            raise RuntimeError("Simulation environment did not initialize.")
        environment.run(until=duration_seconds + 0.00001)
        runner._state["status"] = "completed"
        runner._state["activeFlows"] = 0
        flow_metrics = runner._flow_metrics()
        class_flow_metrics = {
            traffic_class: [
                metric
                for metric in flow_metrics
                if metric["trafficClass"] == traffic_class
            ]
            for traffic_class in ("ls", "ts")
        }

        def class_latency(traffic_class: str) -> float:
            flows = class_flow_metrics[traffic_class]
            delivered = sum(flow["deliveredPackets"] for flow in flows)
            latency_sum = sum(
                runner._flows_by_id[flow["flowId"]].latency_sum_ms
                for flow in flows
            )
            return round(latency_sum / delivered, 3) if delivered else 0.0

        def class_loss(traffic_class: str) -> float:
            flows = class_flow_metrics[traffic_class]
            generated = sum(flow["generatedPackets"] for flow in flows)
            dropped = sum(flow["droppedPackets"] for flow in flows)
            return round(dropped * 100 / generated, 4) if generated else 0.0

        qos_satisfied = 0
        for flow in runner._flows:
            generated = flow.generated_packets
            delivered = flow.delivered_packets
            loss_pct = (
                flow.dropped_packets * 100 / generated if generated else 0.0
            )
            if flow.traffic_class == "ls":
                latency_ms = (
                    flow.latency_sum_ms / delivered if delivered else 0.0
                )
                satisfies_qos = latency_ms <= 50 and loss_pct <= 1
            else:
                offered_mbps = (
                    flow.arrival_rate_pps * flow.packet_bytes * 8 / 1_000_000
                )
                delivered_mbps = (
                    delivered * flow.packet_bytes * 8
                    / max(duration_seconds, 1)
                    / 1_000_000
                )
                satisfies_qos = (
                    delivered_mbps >= offered_mbps * 0.95
                    and loss_pct <= 1
                )
            qos_satisfied += int(satisfies_qos)

        queue_occupancy = [
            point["lsOccupancy"] + point["tsOccupancy"]
            for point in runner._metrics
        ]
        return {
            "algorithm": algorithm,
            "meanLatencyMs": runner._state["averageLatencyMs"],
            "throughputMbps": round(
                sum(runner._throughputs) / len(runner._throughputs), 6
            )
            if runner._throughputs
            else 0.0,
            "packetLossPercent": runner._state["packetLossPercent"],
            "meanQueueOccupancy": round(
                sum(queue_occupancy) / len(queue_occupancy), 3
            )
            if queue_occupancy
            else 0.0,
            "lsLatencyMs": class_latency("ls"),
            "tsLatencyMs": class_latency("ts"),
            "lsLossPct": class_loss("ls"),
            "tsLossPct": class_loss("ts"),
            "qosSatisfiedPct": round(
                qos_satisfied * 100 / len(runner._flows), 2
            )
            if runner._flows
            else 0.0,
            "reroutes": runner._reroute_count,
            "flowMetrics": flow_metrics,
        }


simulation = SimulationManager()
