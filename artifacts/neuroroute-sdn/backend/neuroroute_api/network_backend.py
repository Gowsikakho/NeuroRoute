from __future__ import annotations

from abc import ABC, abstractmethod


class NetworkBackend(ABC):
    """Control and observation contract for network simulators and adapters."""

    @abstractmethod
    def get_topology(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def poll_stats(self, window_seconds: int = 120) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def install_path(self, flow_id: str, path: list[str]) -> dict:
        raise NotImplementedError

    @abstractmethod
    def set_queue_rate(self, switch_id: str, ls_share: float) -> dict:
        raise NotImplementedError

    @abstractmethod
    def start_traffic(self, config: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def stop_traffic(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def fail_link(self, link_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def restore_link(self, link_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def step(self) -> dict:
        raise NotImplementedError
