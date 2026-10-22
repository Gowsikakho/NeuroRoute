from __future__ import annotations

from collections import defaultdict


class SimulatedOpenFlowController:
    """In-memory OpenFlow-style flow, port, and queue control plane."""

    def __init__(self) -> None:
        self.reset([])

    def reset(self, switch_ids: list[str]) -> None:
        self._flow_tables: dict[str, dict[str, dict]] = {
            switch_id: {} for switch_id in switch_ids
        }
        self._port_counters: dict[str, dict[str, int]] = defaultdict(
            lambda: {"rxPackets": 0, "txPackets": 0}
        )
        self._queue_rates: dict[str, float] = {
            switch_id: 0.62 for switch_id in switch_ids
        }

    def record_path(
        self,
        path: list[str],
        traffic_class: str,
        packet_count: int,
    ) -> None:
        if len(path) < 2:
            return
        destination = path[-1]
        for index, switch_id in enumerate(path[:-1]):
            next_hop = path[index + 1]
            key = f"{traffic_class}:{destination}"
            table = self._flow_tables.setdefault(switch_id, {})
            entry = table.setdefault(
                key,
                {
                    "match": {"trafficClass": traffic_class, "destination": destination},
                    "actions": {"output": next_hop},
                    "packets": 0,
                },
            )
            entry["packets"] += packet_count

    def record_port_transmission(self, source: str, target: str) -> None:
        self._port_counters[f"{source}:{target}"]["txPackets"] += 1
        self._port_counters[f"{target}:{source}"]["rxPackets"] += 1

    def install_flow_rule(self, switch_id: str, rule: dict) -> dict:
        if switch_id not in self._flow_tables:
            raise ValueError(f"Unknown switch: {switch_id}")
        if not isinstance(rule.get("match"), dict) or not isinstance(
            rule.get("actions"), dict
        ):
            raise ValueError("A flow rule needs match and actions objects.")
        key = str(rule.get("id") or rule["match"])
        installed = {
            "match": rule["match"],
            "actions": rule["actions"],
            "packets": 0,
        }
        self._flow_tables[switch_id][key] = installed
        return {"switchId": switch_id, **installed}

    def install_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        if switch_id not in self._flow_tables:
            raise ValueError(f"Unknown switch: {switch_id}")
        if not 0.0 <= ls_share <= 1.0:
            raise ValueError("LS queue rate must be between 0 and 1.")
        self._queue_rates[switch_id] = ls_share
        return {
            "switchId": switch_id,
            "lsRatePercent": round(ls_share * 100, 2),
            "tsRatePercent": round((1 - ls_share) * 100, 2),
            "mode": "simulated",
        }

    def queue_rate(self, switch_id: str, default: float) -> float:
        return self._queue_rates.get(switch_id, default)

    def state(self, metric_points: list[dict]) -> dict:
        latest_elapsed = max(
            (point["elapsedSeconds"] for point in metric_points),
            default=None,
        )
        latest_by_switch: dict[str, list[dict]] = defaultdict(list)
        if latest_elapsed is not None:
            for point in metric_points:
                if point["elapsedSeconds"] == latest_elapsed:
                    latest_by_switch[point["switchId"]].append(point)
        switches = []
        for switch_id in sorted(self._flow_tables):
            points = latest_by_switch.get(switch_id, [])
            count = max(len(points), 1)
            switches.append(
                {
                    "switchId": switch_id,
                    "flowTable": list(self._flow_tables[switch_id].values()),
                    "queues": {
                        "lsOccupancy": round(
                            sum(point["lsOccupancy"] for point in points) / count, 3
                        ),
                        "tsOccupancy": round(
                            sum(point["tsOccupancy"] for point in points) / count, 3
                        ),
                        "droppedPackets": sum(
                            point.get("lsDrops", 0) + point.get("tsDrops", 0)
                            for point in points
                        ),
                        "transmitRateMbps": round(
                            sum(point.get("transmitRateMbps", 0.0) for point in points),
                            6,
                        ),
                        "lsRatePercent": round(
                            self.queue_rate(switch_id, 0.62) * 100, 2
                        ),
                        "tsRatePercent": round(
                            (1 - self.queue_rate(switch_id, 0.62)) * 100, 2
                        ),
                    },
                }
            )
        ports = [
            {
                "port": port,
                "rxPackets": counts["rxPackets"],
                "txPackets": counts["txPackets"],
            }
            for port, counts in sorted(self._port_counters.items())
        ]
        return {"mode": "simulated-openflow", "switches": switches, "ports": ports}
