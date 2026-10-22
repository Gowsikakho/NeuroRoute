from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import torch
from torch import nn


def candidate_path_state(
    current_occupancy: list[float],
    predicted_occupancy: list[list[float]],
    candidate_paths: list[list[int]],
) -> list[float]:
    """Flatten current and forecast occupancy features for Yen candidate paths."""
    state: list[float] = []
    for path in candidate_paths:
        state.extend(
            current_occupancy[node]
            for node in path
            if 0 <= node < len(current_occupancy)
        )
        for forecast_step in predicted_occupancy:
            state.extend(
                forecast_step[node]
                for node in path
                if 0 <= node < len(forecast_step)
            )
    return state


class DuelingQNetwork(nn.Module):
    def __init__(self, state_size: int, action_count: int, hidden_size: int = 128) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
        )
        self.value_stream = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, 1),
        )
        self.advantage_stream = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, action_count),
        )

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        encoded = self.encoder(state)
        value = self.value_stream(encoded)
        advantage = self.advantage_stream(encoded)
        return value + advantage - advantage.mean(dim=-1, keepdim=True)


@dataclass
class Transition:
    state: list[float]
    action: int
    reward: float
    next_state: list[float]
    done: bool


class D3QNAgent:
    """Double-Q learning with a dueling value/advantage network and replay."""

    def __init__(
        self,
        state_size: int,
        action_count: int,
        learning_rate: float = 1e-3,
        gamma: float = 0.99,
        replay_capacity: int = 50_000,
        target_sync_steps: int = 250,
        seed: int = 0,
    ) -> None:
        self.online = DuelingQNetwork(state_size, action_count)
        self.target = DuelingQNetwork(state_size, action_count)
        self.target.load_state_dict(self.online.state_dict())
        self.target.eval()
        self.optimizer = torch.optim.Adam(self.online.parameters(), lr=learning_rate)
        self.gamma = gamma
        self.target_sync_steps = target_sync_steps
        self.steps = 0
        self.random = random.Random(seed)
        self.replay: deque[Transition] = deque(maxlen=replay_capacity)
        self.action_count = action_count

    def act(self, state: list[float], epsilon: float = 0.05) -> int:
        if self.random.random() < epsilon:
            return self.random.randrange(self.action_count)
        with torch.no_grad():
            tensor = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
            return int(self.online(tensor).argmax(dim=1).item())

    def remember(self, transition: Transition) -> None:
        self.replay.append(transition)

    def learn(self, batch_size: int = 64) -> float | None:
        if len(self.replay) < batch_size:
            return None
        batch = self.random.sample(list(self.replay), batch_size)
        states = torch.tensor([item.state for item in batch], dtype=torch.float32)
        actions = torch.tensor([item.action for item in batch], dtype=torch.int64)
        rewards = torch.tensor([item.reward for item in batch], dtype=torch.float32)
        next_states = torch.tensor(
            [item.next_state for item in batch], dtype=torch.float32
        )
        dones = torch.tensor([item.done for item in batch], dtype=torch.float32)

        chosen_q = self.online(states).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            next_actions = self.online(next_states).argmax(dim=1)
            next_q = self.target(next_states).gather(
                1, next_actions.unsqueeze(1)
            ).squeeze(1)
            targets = rewards + self.gamma * next_q * (1 - dones)

        loss = torch.nn.functional.mse_loss(chosen_q, targets)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.online.parameters(), max_norm=5.0)
        self.optimizer.step()
        self.steps += 1
        if self.steps % self.target_sync_steps == 0:
            self.target.load_state_dict(self.online.state_dict())
        return float(loss.detach().item())
