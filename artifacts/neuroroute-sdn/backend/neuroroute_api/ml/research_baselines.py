from __future__ import annotations

import torch
from torch import nn
from torch_geometric.nn import ChebConv


class LSTMQueuePredictor(nn.Module):
    """Sequence baseline used with a separate tabular Q-learning policy."""

    def __init__(
        self, input_features: int, hidden_size: int = 64, forecast_steps: int = 5
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_features, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, forecast_steps)

    def forward(self, sequence: torch.Tensor) -> torch.Tensor:
        _, (hidden, _) = self.lstm(sequence)
        return self.head(hidden[-1])


class ChebyshevGCNEncoder(nn.Module):
    """Chebyshev graph-convolution encoder for a DQN routing baseline."""

    def __init__(
        self,
        input_features: int,
        hidden_channels: int = 64,
        output_features: int = 32,
        polynomial_order: int = 3,
    ) -> None:
        super().__init__()
        self.conv1 = ChebConv(input_features, hidden_channels, K=polynomial_order)
        self.conv2 = ChebConv(hidden_channels, output_features, K=polynomial_order)

    def forward(
        self, node_features: torch.Tensor, edge_index: torch.Tensor
    ) -> torch.Tensor:
        hidden = torch.relu(self.conv1(node_features, edge_index))
        return torch.relu(self.conv2(hidden, edge_index))
