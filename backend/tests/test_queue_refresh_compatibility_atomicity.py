"""Compatibility queue publication and its lease completion commit together."""

from datetime import date, datetime, timezone

import pytest

from app.product_composition import compatibility_coordinator
from app.services import durable_tasks
from tempo_legacy_database import connections as database


def test_compatibility_queue_publication_rolls_back_lost_lease_and_rejects_stale_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue-compatibility.sqlite")
    database.initialize()

    def write(operation, *, label):
        with database.connection(background=True) as connection:
            return operation(connection)

    monkeypatch.setattr(compatibility_coordinator, "submit_background_write", write)
    monkeypatch.setattr(durable_tasks, "submit_background_write", write)
    monkeypatch.setattr(durable_tasks, "submit_foreground_write", write)
    day = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection(background=True) as connection:
        connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','built-in',?)",
            (now,),
        )
        connection.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,
                   content_type,state,trained_color,source_ref)
               VALUES('atomic-tactic','__tactics__','prefix',?,?,'2000-01-01',
                      'tactics','learning','white','atomic-puzzle')""",
            ('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', '["e2e4"]'),
        )
        durable_tasks.enqueue_task_in_transaction(
            connection, "daily_queue", "current", {"queue_date": day}, priority=10,
        )
    claimed = durable_tasks.claim_task("daily_queue")
    assert claimed is not None

    def snapshot():
        with database.read_connection() as connection:
            return {
                "task": tuple(connection.execute(
                    "SELECT generation,state,phase,lease_token,payload_json FROM background_tasks WHERE id=?",
                    (claimed["id"],),
                ).fetchone()),
                "projection": tuple(connection.execute(
                    "SELECT state,generation,refresh_pending,requested_refresh_epoch,published_refresh_epoch "
                    "FROM queue_projections WHERE queue_date=?", (day,),
                ).fetchone()),
                "queue": [tuple(row) for row in connection.execute(
                    "SELECT id,card_id,status FROM daily_queue WHERE queue_date=? ORDER BY id", (day,),
                )],
                "plans": [tuple(row) for row in connection.execute(
                    "SELECT queue_date,seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,),
                )],
            }

    before = snapshot()
    assert before["task"][1] == "leased" and before["queue"] == []
    with monkeypatch.context() as failed_completion:
        failed_completion.setattr(durable_tasks, "complete_task_slice_in_transaction", lambda *_: False)
        with pytest.raises(RuntimeError, match="lost its task lease"):
            compatibility_coordinator._execute_daily_queue_task(claimed)
    assert snapshot() == before

    compatibility_coordinator._execute_daily_queue_task(claimed)
    published = snapshot()
    assert published["task"][1] == "complete"
    assert published["projection"][0] == "ready" and published["projection"][2] == 0
    assert published["projection"][3] == published["projection"][4]
    assert [row[1] for row in published["queue"]] == ["atomic-tactic"]
    assert len(published["plans"]) == 1

    # Replaying the old lease must not republish or disturb the committed queue.
    compatibility_coordinator._execute_daily_queue_task(claimed)
    assert snapshot() == published
