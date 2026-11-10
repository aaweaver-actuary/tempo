"""Capacity policy meets the existing writers; these are not PostgreSQL durability proofs."""

from contextlib import contextmanager

import pytest


@pytest.mark.parametrize("value", [None, "0", "true", "yes", "1"])
def test_capacity_configuration_enables_only_explicit_one(monkeypatch, value):
    from app.services.opening_capacity_admission import capacity_expansion_enabled

    if value is None:
        monkeypatch.delenv("OPENING_CAPACITY_FRONTIER_EXPANSION", raising=False)
    else:
        monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", value)
    assert capacity_expansion_enabled() is (value == "1")


@pytest.fixture(autouse=True)
def legacy_capacity_checkpoint_has_no_frozen_order(monkeypatch, request):
    if (
        request.node.name
        != "test_capacity_checkpoint_resume_retains_witness_and_rejects_old_lease"
    ):
        return
    from app.services import frozen_opening_order, postgres_queue_refresh

    monkeypatch.setattr(
        frozen_opening_order, "activation", lambda database, **kwargs: (None, 0)
    )

    @contextmanager
    def metadata_reader():
        yield object()

    monkeypatch.setattr(
        postgres_queue_refresh, "background_read_connection", metadata_reader
    )
    monkeypatch.setattr(
        postgres_queue_refresh.read_storage,
        "background_read_connection",
        metadata_reader,
    )
