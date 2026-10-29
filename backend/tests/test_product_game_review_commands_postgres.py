"""Existing command receipt/replay/rollback proof extracted from #558.

Uses only the established task-owned disposable PostgreSQL fixture.
"""
import json

import pytest

from test_product_game_lapse_postgres import owned_postgres


def test_native_review_commands_commit_with_receipt_replay_and_rollback(owned_postgres, monkeypatch):
    from fastapi import HTTPException
    from app import command_gateway as gateway
    from app.product_composition import game_review_commands as commands
    monkeypatch.setattr(gateway,"_handlers",{})
    monkeypatch.setattr(gateway,"_preparers",{})
    monkeypatch.setattr(gateway,"_writer_connection",lambda background:owned_postgres())
    commands.register_commands()
    configuration=dict(workspace_id="legacy",provider="lichess",account="Learner",
        window=dict(start_at="2026-10-25T00:00:00Z",end_at="2026-10-26T00:00:00Z",precision="second"),
        repertoire_ids=["white"],speeds=["rapid"],rated=True,interval_seconds=None,revision=1)
    payload=dict(configuration=configuration,expected_revision=0)
    saved=gateway.execute_command("native-config","product_games.configure",payload)
    assert saved==gateway.execute_command("native-config","product_games.configure",payload)
    run=dict(provider="lichess",operation_id="native-run")
    queued=gateway.execute_command("native-run","product_games.sync",run)
    assert queued["status"]=="pending"
    assert queued==gateway.execute_command("native-run","product_games.sync",run)
    with pytest.raises(gateway.CommandConflict):
        gateway.execute_command("native-run","product_games.sync",dict(provider="chesscom",operation_id="native-run"))
    original=gateway._handlers["product_games.sync"]
    def failed_after_enqueue(database,payload):
        original(database,payload)
        raise HTTPException(409,"synthetic_failure_after_enqueue")
    monkeypatch.setitem(gateway._handlers,"product_games.sync",failed_after_enqueue)
    assert gateway.execute_command("native-rollback","product_games.sync",
        dict(provider="lichess",operation_id="native-rollback")) is None
    with owned_postgres() as database:
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_jobs").fetchone()[0]==1
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_sync_requests").fetchone()[0]==1
        rows=database.execute_native("SELECT operation_id,state FROM operation_receipts ORDER BY operation_id").fetchall()
        assert [tuple(row) for row in rows]==[("native-config","complete"),("native-rollback","failed"),("native-run","complete")]
        # Remove only this command case's exact records, including its
        # configured account, before returning the shared owning fixture.
        database.execute_native("DELETE FROM product_feedback_sync_requests WHERE workspace_id='legacy' AND operation_id='native-run'")
        database.execute_native("DELETE FROM product_feedback_jobs WHERE job_id='native-run'")
        database.execute_native("""DELETE FROM product_feedback_configuration WHERE workspace_id='legacy'
            AND provider='lichess' AND revision=1 AND payload=%s::jsonb""",(json.dumps(configuration),))
        database.execute_native("DELETE FROM operation_receipts WHERE operation_id IN ('native-config','native-run','native-rollback')")
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_configuration").fetchone()[0]==0

