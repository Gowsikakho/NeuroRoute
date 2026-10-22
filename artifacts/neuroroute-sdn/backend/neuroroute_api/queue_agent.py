from __future__ import annotations

import random


class QueueRateQLearningAgent:
    """Small tabular controller that allocates service between LS and TS queues."""

    def __init__(self, seed: int, eta: float = 0.08) -> None:
        self._random = random.Random(seed)
        self._eta = eta
        self._alpha = 0.15
        self._gamma = 0.85
        self._epsilon = 0.12
        self._actions = (0.2, 0.35, 0.5, 0.65, 0.8)
        self._values: dict[tuple[int, int, int], list[float]] = {}
        self._last: tuple[tuple[int, int, int], int] | None = None

    def _state(self, ls: float, ts: float) -> tuple[int, int, int]:
        total = max(ls + ts, 1.0)
        congestion = min(4, int(total // 20))
        imbalance = min(4, max(0, int((ls / total) * 5)))
        return congestion, imbalance, int(ls > ts)

    def choose_rate(self, ls: float, ts: float) -> float:
        state = self._state(ls, ts)
        values = self._values.setdefault(state, [0.0] * len(self._actions))
        if self._random.random() < self._epsilon:
            action = self._random.randrange(len(self._actions))
        else:
            action = max(range(len(values)), key=values.__getitem__)

        if self._last is not None:
            prior_state, prior_action = self._last
            prior_values = self._values.setdefault(
                prior_state, [0.0] * len(self._actions)
            )
            capacity = 100.0
            reward = self._eta * (capacity - min(ls + ts, capacity))
            target = reward + self._gamma * max(values)
            prior_values[prior_action] += self._alpha * (
                target - prior_values[prior_action]
            )

        self._last = (state, action)
        return self._actions[action]
