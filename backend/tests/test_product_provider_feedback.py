"""Composition injects the public intake; no inline Study implementation exists."""
from types import SimpleNamespace

import pytest

from app.games.feedback.review import ReviewUnavailable
from app.product_composition import game_sync_current as current
from test_product_priority_intake_boundary import AcceptedPriorityIntake


def test_current_provider_factory_requires_public_intake_before_any_source_access():
    class NoConnections:
        def __call__(self):
            pytest.fail("Missing intake must fail before a database read or claim")
    with pytest.raises(ReviewUnavailable, match="decoupled_priority_intake_required"):
        current.create_current_provider_worker(workspace="legacy", connections=NoConnections())


def test_current_provider_factory_injects_exact_public_port_without_constructing_study(monkeypatch):
    intake, connections, clock, forward = AcceptedPriorityIntake(), object(), object(), object()
    expected = SimpleNamespace(runtime="optional worker", schedule="bounded schedule")
    supplied = {}
    def create(**values):
        supplied.update(values)
        return expected
    monkeypatch.setattr(current, "create_current_game_sync", create)
    assert current.create_current_provider_worker(workspace="legacy", connections=connections,
        priority_intake=intake, clock=clock, forward_window=forward) is expected
    assert supplied == dict(workspace="legacy", connections=connections, learning=intake,
        clock=clock, forward_window=forward, transport_factory=None)
