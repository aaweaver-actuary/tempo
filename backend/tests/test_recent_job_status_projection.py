from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from app.games.feedback.review import GameReview, JobStatus, sync_status
from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore


def test_recent_job_status_projection_preserves_scope_order_and_small_fields():
    class Database:
        def execute(self, sql, params):
            assert "job.payload::text" not in sql
            assert "request.requested_at DESC,request.operation_id DESC LIMIT 1" in sql
            assert params == ("workspace", "lichess")
            self.sql = sql
            return self

        def fetchone(self):
            return ("actual-job", "retryable", True, "saved error")

    database = Database()
    store = PostgresReviewStore(None)

    @contextmanager
    def transaction():
        yield database

    store._transaction = transaction
    status = store.latest_job_status("workspace", "lichess")
    assert sync_status("lichess", status).model_dump() == {
        "provider": "lichess", "operation_id": "actual-job", "status": "failed", "error": "saved error"
    }


def test_recent_job_status_projection_rejects_invalid_and_defaults_only_absent_status():
    class Database:
        row = None
        def execute(self, sql, params):
            return self
        def fetchone(self):
            return self.row

    database = Database()
    store = PostgresReviewStore(None)

    @contextmanager
    def transaction():
        yield database

    store._transaction = transaction
    assert store.latest_job_status("w", "lichess") is None
    database.row = ("job", None, False, None)
    assert store.latest_job_status("w", "lichess").status == "queued"
    for row in [("job", None, True, None), ("job", 1, True, None),
                ("job", "unknown", True, None), (None, "queued", True, None),
                (1, "queued", True, None)]:
        database.row = row
        with pytest.raises(ValidationError):
            store.latest_job_status("w", "lichess")


def test_recent_games_uses_status_projection_in_provider_order():
    class Store:
        def recent(self, workspace, limit):
            assert (workspace, limit) == ("w", 50)
            return ()
        def latest_job_status(self, workspace, provider):
            assert workspace == "w"
            return JobStatus(job_id="job", status="running") if provider == "lichess" else None
        def last_job(self, *args):
            pytest.fail("full job must not be read")

    review = GameReview(workspace="w", store=Store(), learning=None, build_job=None, deliver=None, now=None)
    result = review.recent_games()
    assert [(item.provider, item.status, item.operation_id) for item in result.sync] == [
        ("lichess", "pending", "job"), ("chesscom", "idle", None)
    ]
