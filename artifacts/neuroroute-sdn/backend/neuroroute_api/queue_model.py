from __future__ import annotations

import random
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterator

import simpy

TRAFFIC_CLASSES = ("ls", "ts")
QUEUE_CAPACITY_BYTES = 64 * 1024
SERVICE_QUANTUM_SECONDS = 0.01


@dataclass
class Packet:
    flow_id: str
    traffic_class: str
    size_bytes: int
    created_at: float
    path: list[str]
    hop_index: int = 0


class OutputPortQueue:
    """Separate bounded LS/TS FIFOs with byte-based token-bucket service."""

    def __init__(
        self,
        source: str,
        target: str,
        bandwidth_mbps: float,
        capacity_bytes: int = QUEUE_CAPACITY_BYTES,
    ) -> None:
        self.source = source
        self.target = target
        self.port_id = f"{source}:{target}"
        self.bandwidth_bps = bandwidth_mbps * 1_000_000
        self.capacity_bytes = capacity_bytes
        self.queues: dict[str, deque[Packet]] = {
            traffic_class: deque() for traffic_class in TRAFFIC_CLASSES
        }
        self.occupancy_bytes = {traffic_class: 0 for traffic_class in TRAFFIC_CLASSES}
        self.tokens_bytes = {
            traffic_class: float(capacity_bytes)
            for traffic_class in TRAFFIC_CLASSES
        }
        self.interval_drops = {traffic_class: 0 for traffic_class in TRAFFIC_CLASSES}
        self.interval_tx_bytes = 0
        self.interval_tx_packets = 0
        self.is_up = True

    def occupancy_percent(self, traffic_class: str) -> float:
        return 100 * self.occupancy_bytes[traffic_class] / self.capacity_bytes

    def enqueue(self, packet: Packet) -> bool:
        traffic_class = packet.traffic_class
        if (
            not self.is_up
            or self.occupancy_bytes[traffic_class] + packet.size_bytes
            > self.capacity_bytes
        ):
            self.interval_drops[traffic_class] += 1
            return False
        self.queues[traffic_class].append(packet)
        self.occupancy_bytes[traffic_class] += packet.size_bytes
        return True

    def set_link_state(self, is_up: bool) -> list[Packet]:
        self.is_up = is_up
        if is_up:
            return []

        dropped: list[Packet] = []
        for traffic_class in TRAFFIC_CLASSES:
            queue = self.queues[traffic_class]
            dropped.extend(queue)
            self.interval_drops[traffic_class] += len(queue)
            queue.clear()
            self.occupancy_bytes[traffic_class] = 0
            self.tokens_bytes[traffic_class] = 0.0
        return dropped

    def service_process(
        self,
        environment: simpy.Environment,
        get_ls_share: Callable[[str], float],
        on_transmit: Callable[[Packet, OutputPortQueue], None],
    ) -> Iterator[simpy.Event]:
        last_refill = float(environment.now)
        while True:
            yield environment.timeout(SERVICE_QUANTUM_SECONDS)
            now = float(environment.now)
            elapsed = now - last_refill
            last_refill = now
            if not self.is_up:
                continue

            ls_share = min(1.0, max(0.0, get_ls_share(self.source)))
            shares = {"ls": ls_share, "ts": 1.0 - ls_share}
            for traffic_class in TRAFFIC_CLASSES:
                added_tokens = self.bandwidth_bps * shares[traffic_class] * elapsed / 8
                self.tokens_bytes[traffic_class] = min(
                    self.capacity_bytes,
                    self.tokens_bytes[traffic_class] + added_tokens,
                )

            for traffic_class in TRAFFIC_CLASSES:
                queue = self.queues[traffic_class]
                while queue and self.tokens_bytes[traffic_class] >= queue[0].size_bytes:
                    packet = queue.popleft()
                    self.occupancy_bytes[traffic_class] -= packet.size_bytes
                    self.tokens_bytes[traffic_class] -= packet.size_bytes
                    self.interval_tx_bytes += packet.size_bytes
                    self.interval_tx_packets += 1
                    on_transmit(packet, self)

    def sample_and_reset(self, elapsed_seconds: float, timestamp: float) -> dict:
        interval = max(elapsed_seconds, 1e-9)
        point = {
            "timestamp": timestamp,
            "elapsedSeconds": elapsed_seconds,
            "switchId": self.source,
            "portId": self.port_id,
            "lsOccupancy": round(self.occupancy_percent("ls"), 3),
            "tsOccupancy": round(self.occupancy_percent("ts"), 3),
            "lsQueueBytes": self.occupancy_bytes["ls"],
            "tsQueueBytes": self.occupancy_bytes["ts"],
            "capacity": self.capacity_bytes,
            "lsDrops": self.interval_drops["ls"],
            "tsDrops": self.interval_drops["ts"],
            "transmitRateMbps": round(
                self.interval_tx_bytes * 8 / 1_000_000 / interval, 6
            ),
            "transmittedPackets": self.interval_tx_packets,
        }
        self.interval_drops = {traffic_class: 0 for traffic_class in TRAFFIC_CLASSES}
        self.interval_tx_bytes = 0
        self.interval_tx_packets = 0
        return point


def simulate_mm1_mean_delay(
    arrival_rate: float,
    service_rate: float,
    packet_count: int,
    seed: int,
) -> float:
    """Return the sampled M/M/1 mean time in system, in simulated seconds."""
    if arrival_rate <= 0 or service_rate <= arrival_rate:
        raise ValueError("M/M/1 requires 0 < arrival_rate < service_rate.")
    if packet_count <= 0:
        raise ValueError("packet_count must be positive.")

    environment = simpy.Environment()
    server = simpy.Resource(environment, capacity=1)
    random_source = random.Random(seed)
    delays: list[float] = []

    def customer(arrived_at: float) -> Iterator[simpy.Event]:
        with server.request() as request:
            yield request
            yield environment.timeout(random_source.expovariate(service_rate))
            delays.append(float(environment.now) - arrived_at)

    def arrivals() -> Iterator[simpy.Event]:
        for _ in range(packet_count):
            yield environment.timeout(random_source.expovariate(arrival_rate))
            environment.process(customer(float(environment.now)))

    environment.process(arrivals())
    environment.run()
    return statistics.fmean(delays)
