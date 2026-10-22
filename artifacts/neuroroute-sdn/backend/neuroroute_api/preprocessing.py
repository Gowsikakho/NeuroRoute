from __future__ import annotations

import math
from collections.abc import Sequence


def dense_timing_indicator(
    timestamps: Sequence[float],
    sample_period_seconds: float,
    step_count: int,
) -> list[list[float]]:
    """Create a dense sample-to-time-bin indicator matrix.

    This generic preprocessing helper is not claimed to reproduce the missing
    paper's Eq. 3. Its exact timing definition must be confirmed against the
    source paper before research results are labelled paper-reproducible.
    """
    if sample_period_seconds <= 0 or step_count < 1:
        raise ValueError("sample period and step count must be positive.")
    if not timestamps:
        return []
    origin = timestamps[0]
    rows: list[list[float]] = []
    for timestamp in timestamps:
        index = int((timestamp - origin) / sample_period_seconds)
        row = [0.0] * step_count
        if 0 <= index < step_count:
            row[index] = 1.0
        rows.append(row)
    return rows


def tanh_normalize(
    values: Sequence[float],
    center: float,
    scale: float,
) -> list[float]:
    """Apply configurable tanh normalization; paper constants are not assumed."""
    if scale <= 0:
        raise ValueError("normalization scale must be positive.")
    return [math.tanh((value - center) / scale) for value in values]
