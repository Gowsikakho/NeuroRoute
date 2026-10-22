from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..network_backend import NetworkBackend


class RyuRestBackend(NetworkBackend):
    """Optional Ryu ofctl_rest/QoS adapter for a reachable WSL2 controller."""

    def __init__(self, controller_url: str = "http://127.0.0.1:8080") -> None:
        self.controller_url = controller_url.rstrip("/")

    def _request(self, path: str, payload: dict | None = None) -> dict | list:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.controller_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urlopen(request, timeout=4) as response:
                return json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError) as error:
            raise RuntimeError(f"Could not reach the Ryu controller: {error}") from error

    def discover_topology(self) -> list[dict]:
        datapaths = self._request("/stats/switches")
        return [{"id": str(dpid), "label": f"DPID {dpid}"} for dpid in datapaths]

    def get_port_stats(self) -> list[dict]:
        switches = self._request("/stats/switches")
        stats: list[dict] = []
        for dpid in switches:
            response = self._request(f"/stats/port/{dpid}")
            stats.extend(response.get(str(dpid), []))
        return stats

    def get_queue_stats(self, window_seconds: int = 120) -> list[dict]:
        del window_seconds
        switches = self._request("/stats/switches")
        stats: list[dict] = []
        for dpid in switches:
            response = self._request(f"/stats/queue/{dpid}")
            stats.extend(response.get(str(dpid), []))
        return stats

    def install_flow_rule(self, switch_id: str, rule: dict) -> dict:
        payload = {"dpid": int(switch_id), **rule}
        return self._request("/stats/flowentry/add", payload)

    def install_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        if not 0 <= ls_share <= 1:
            raise ValueError("LS queue rate must be between 0 and 1.")
        payload = {
            "dpid": int(switch_id),
            "queues": [
                {"queue_id": 1, "max_rate": int(ls_share * 1_000_000_000)},
                {"queue_id": 2, "max_rate": int((1 - ls_share) * 1_000_000_000)},
            ],
        }
        return self._request(f"/qos/queue/{switch_id}/1", payload)
