from __future__ import annotations

import torch
from torch import nn
from torch_geometric.nn import GATConv


class GATGRUOccupancyPredictor(nn.Module):
    """Multi-head graph attention, recurrent state, and k-step dense forecast."""

    def __init__(
        self,
        input_features: int,
        hidden_channels: int = 32,
        attention_heads: int = 4,
        forecast_steps: int = 5,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.attention = GATConv(
            input_features,
            hidden_channels,
            heads=attention_heads,
            concat=True,
            dropout=dropout,
        )
        recurrent_width = hidden_channels * attention_heads
        self.gru = nn.GRUCell(recurrent_width, recurrent_width)
        self.forecast = nn.Sequential(
            nn.Linear(recurrent_width, recurrent_width),
            nn.ReLU(),
            nn.Linear(recurrent_width, forecast_steps),
        )
        self.dropout = nn.Dropout(dropout)

    def forward(self, sequence: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """Return [node, forecast_step] queue-occupancy predictions."""
        if sequence.ndim != 3:
            raise ValueError("sequence must have shape [time, node, feature].")
        _, node_count, _ = sequence.shape
        hidden = None
        for frame in sequence:
            attended = self.dropout(torch.nn.functional.elu(self.attention(frame, edge_index)))
            if hidden is None:
                hidden = torch.zeros_like(attended)
            hidden = self.gru(attended, hidden)
        return self.forecast(hidden)


def mse_loss(prediction: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.mse_loss(prediction, target)
