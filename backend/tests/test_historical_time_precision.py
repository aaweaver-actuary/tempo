"""Legacy Lichess UTC-second and provider-millisecond source identity."""
from types import SimpleNamespace
import hashlib
import pytest

from tempo_legacy_database.adapters.games.product_sources import legacy_lichess_precision_matches


def source(*, pgn_time="16:19:35", played_at="2026-08-02T16:19:35.810+00:00", precision="millisecond"):
    return SimpleNamespace(pgn=f'[UTCDate "2026.08.02"]\n[UTCTime "{pgn_time}"]\n\n1. e4 1-0',
        played_at=played_at, time_precision=precision)


def test_legacy_lichess_second_is_proven_by_original_pgn():
    assert legacy_lichess_precision_matches(source(), "2026-08-02T16:19:35+00:00")


@pytest.mark.parametrize("record,stored", [
    (source(pgn_time="16:19:34"), "2026-08-02T16:19:35+00:00"),
    (source(), "2026-08-02T16:19:34+00:00"),
    (source(), "2026-08-02T16:19:35.100+00:00"),
    (source(precision="unknown"), "2026-08-02T16:19:35+00:00"),
    (source(played_at="2026-08-02T16:19:36.001+00:00"), "2026-08-02T16:19:35+00:00"),
])
def test_unproved_or_different_occurrence_remains_conflict(record, stored):
    assert not legacy_lichess_precision_matches(record, stored)


def test_existing_canonical_utc_second_binds_precise_provider_game_without_rewriting(monkeypatch):
    from test_product_source_identity import inputs, raw_library, bound_store, workflow
    from app.games.providers.lichess_acquisition import LichessGamePage
    from app.games.feedback.ports import PageCommit
    original, request, snapshot = inputs(precision="millisecond", played_at="2026-10-20T12:00:00.810+00:00")
    pgn = original.pgn.replace('[Result "1-0"]', '[UTCDate "2026.10.20"]\n[UTCTime "12:00:00"]\n[Result "1-0"]')
    original = original.model_copy(update={"pgn": pgn, "source_digest": hashlib.sha256(pgn.encode()).hexdigest()})
    row = raw_library(original, request, played_at="2026-10-20T12:00:00+00:00")
    store, state = bound_store(monkeypatch, row)
    h = workflow(original, request, snapshot, store)
    page = LichessGamePage(provider="lichess", account=request.account, window=request.window,
        records=(original,), rejections=(), continuation=None, complete=True, saturated=False)
    h.coordinator.providers["lichess"] = SimpleNamespace(fetch_page=lambda **kwargs: page)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    assert h.memory.load_job(request.job_id).status == "complete"
    captured, = h.memory.captures.values()
    assert captured.record.played_at == original.played_at
    assert captured.normalized.played_at == row["played_at"]
    assert captured.normalized.game_id == row["id"]
    assert not next(iter(h.memory.sources.values())).quarantined
    store.commit_page(PageCommit(job=h.memory.load_job(request.job_id),
        sources=tuple(h.memory.sources.values()), captures=(captured,), deliveries=tuple(h.memory.deliveries.values())))
    assert state.row == row and not state.writes
