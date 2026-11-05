"""Maturity invalidation belongs to the review transaction."""

from datetime import date
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import learning_review_adapter as review
from tempo_legacy_database import connections as database

DAY = date(2026, 10, 9)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "maturity.sqlite")
    database.initialize()
    monkeypatch.setattr(review.storage, "configured", lambda: False)
    from app.services.game_sync_coordinator import coordinator

    wakes = []
    monkeypatch.setattr(coordinator, "wake", lambda: wakes.append(True))
    return database.DB_PATH, wakes


def _connection(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_maturity_rollback_does_not_commit_refresh_epoch_or_wake(store):
    path, wakes = store
    with _connection(path) as db:
        adapter = review.LearningReviewAdapter(db, None)
        adapter.refresh_maturity(SimpleNamespace(local_day=DAY))
        assert (
            db.execute(
                "SELECT requested_refresh_epoch FROM queue_projections"
            ).fetchone()[0]
            == 1
        )
        assert wakes == []
        db.rollback()
    with _connection(path) as db:
        assert db.execute("SELECT count(*) FROM queue_projections").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM background_tasks").fetchone()[0] == 0
    assert wakes == []


def test_maturity_commit_records_epoch_and_deferred_wake_cannot_dirty_ready_projection(
    store,
):
    path, wakes = store
    with _connection(path) as db:
        adapter = review.LearningReviewAdapter(db, None)
        adapter.refresh_maturity(SimpleNamespace(local_day=DAY))
        assert wakes == []
        db.commit()
    with _connection(path) as db:
        projection = db.execute(
            "SELECT state,refresh_pending,requested_refresh_epoch FROM queue_projections"
        ).fetchone()
        assert tuple(projection) == ("refreshing", 1, 1)
        assert (
            db.execute(
                "SELECT count(*) FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'"
            ).fetchone()[0]
            == 1
        )
        # Model a worker that completes before the advisory callback runs.
        db.execute(
            "UPDATE queue_projections SET state='ready',refresh_pending=0,published_refresh_epoch=requested_refresh_epoch"
        )
        db.execute("UPDATE background_tasks SET state='complete',phase='published'")
    adapter.finish_compatibility_effects()
    assert wakes == [True]
    with _connection(path) as db:
        assert tuple(
            db.execute(
                "SELECT state,refresh_pending,requested_refresh_epoch,published_refresh_epoch FROM queue_projections"
            ).fetchone()
        ) == ("ready", 0, 1, 1)
        assert (
            db.execute("SELECT state FROM background_tasks").fetchone()[0] == "complete"
        )
