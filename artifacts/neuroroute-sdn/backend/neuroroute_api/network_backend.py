from __future__ import annotations

from abc import ABC, abstractmethod


class NetworkBackend(ABC):
    """Contract shared by the native SimPy and optional WSL2 controller backends."""

    @abstractmethod
    def discover_topology(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_port_stats(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_queue_stats(self, window_seconds: int = 120) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def install_flow_rule(self, switch_id: str, rule: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def install_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        raise NotImplementedError
