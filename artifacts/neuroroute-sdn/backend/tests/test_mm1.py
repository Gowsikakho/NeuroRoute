from __future__ import annotations

import pytest

from neuroroute_api.queue_model import simulate_mm1_mean_delay


@pytest.mark.parametrize(
    ("arrival_rate", "service_rate", "packet_count", "seed"),
    [(9.0, 10.0, 200_000, 2026)],
)
def test_mm1_mean_delay_matches_theory_within_five_percent(
    arrival_rate: float,
    service_rate: float,
    packet_count: int,
    seed: int,
) -> None:
    expected_seconds = 1 / (service_rate - arrival_rate)
    measured_seconds = simulate_mm1_mean_delay(
        arrival_rate,
        service_rate,
        packet_count,
        seed,
    )

    assert measured_seconds == pytest.approx(expected_seconds, rel=0.05)
