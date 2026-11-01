"""Prove capture publication, contention, rollback, and receipt replay on disposable PostgreSQL."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from unittest.mock import patch

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.tactics import captures as capture_storage
from app import tactic_commands  # registers the production handler
from app.command_gateway import execute_command, read_operation
from app.services.postgres_queue_refresh import _publish_tactical_introduction
from tempo_legacy_database.adapters.tactic_admission import count_daily_tactic_introductions
from app.chess.cards import card_id


def main(database_url="postgresql://postgres@postgres:5432/tempo"):
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Capture proof requires the disposable PostgreSQL instance")
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    os.environ["TEMPO_DATABASE_WRITE_URL"] = database_url
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    day = date.today().isoformat()
    request = {"capture_id": str(uuid.uuid4()), "starting_fen": chess.STARTING_FEN,
               "moves": ["e2e4"], "source_kind": "manual", "note": "durability synthetic"}
    payload = {"request": request}
    operation_id = request["capture_id"]
    with postgres_store.connection() as database:
        original_limit = database.execute("SELECT tactics_new_per_day FROM settings WHERE id=1").fetchone()[0]
        initial_count = count_daily_tactic_introductions(database, day)
        database.execute("UPDATE settings SET tactics_new_per_day=?", (initial_count + 1,))
        database.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('capture-synthetic',1)")
        rotation = database.execute("SELECT last_pack_id FROM tactic_rotation WHERE id=1").fetchone()[0]
    automatic_fen = chess.Board(); automatic_fen.push_uci("a2a3")
    automatic_moves = ["a7a6"]
    prepared = {"pack_id": "capture-synthetic", "puzzle_id": "capture-synthetic",
                "card_id": card_id(automatic_fen.fen(), automatic_moves),
                "training_fen": automatic_fen.fen(), "source_fen": chess.STARTING_FEN,
                "solution_json": json.dumps(automatic_moves), "rotation_cursor": rotation}
    # Prepare outside a connection, then publish concurrently with a foreground capture.
    rendezvous = threading.Barrier(2)
    def publish():
        rendezvous.wait(timeout=5)
        with postgres_store.connection() as database:
            return _publish_tactical_introduction(database, day, prepared)
    def capture():
        rendezvous.wait(timeout=5)
        return execute_command(operation_id, "tactics.capture.create", payload)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            captured_future = workers.submit(capture)
            published_future = workers.submit(publish)
            captured = captured_future.result(timeout=10)
            automatic_published = published_future.result(timeout=10)
        assert captured and captured["queued"]
        with postgres_store.connection(read_only=True) as database:
            assert count_daily_tactic_introductions(database, day) == initial_count + 1 + int(automatic_published)
            entries_before = [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (operation_id,)).fetchone()[0] == 1
            assert database.execute("SELECT attempt_state FROM daily_queue WHERE card_id=?", (captured["card_id"],)).fetchone()[0] == "guided"
        postgres_store.close_pools()  # restart the client's persistence boundary
        assert execute_command(operation_id, "tactics.capture.create", payload) == captured
        assert read_operation(operation_id)["state"] == "complete"
        with postgres_store.connection(read_only=True) as database:
            assert entries_before == [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
        # A failing command must retain its failed receipt and no business effects.
        invalid = {**request, "capture_id": str(uuid.uuid4()), "moves": ["e2e5"]}
        assert execute_command(invalid["capture_id"], "tactics.capture.create", {"request": invalid}) is None
        assert read_operation(invalid["capture_id"])["state"] == "failed"
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (invalid["capture_id"],)).fetchone()[0] == 0
            assert entries_before == [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
        # Failure immediately before the capture receipt rolls back every borrowed
        # card/queue effect while the gateway retains its own failed operation receipt.
        fault = {**request, "capture_id": str(uuid.uuid4()), "moves": ["d2d4"]}
        def business_rows():
            with postgres_store.connection(read_only=True) as database:
                return {table: [tuple(row) for row in database.execute(f"SELECT * FROM {table} ORDER BY {order}")]
                        for table, order in (("repertoires", "id"), ("cards", "id"),
                            ("repertoire_cards", "repertoire_id,card_id"), ("daily_queue", "id"),
                            ("tactic_captures", "id"))}
        before_fault = business_rows()
        fault_card_id = card_id(fault["starting_fen"], fault["moves"])
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM cards WHERE id=?", (fault_card_id,)).fetchone()[0] == 0
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (fault["capture_id"],)).fetchone()[0] == 0
        observed_admission = []
        def fail_after_admission(writer, capture_id, actual_card_id, *_):
            assert capture_id == fault["capture_id"] and actual_card_id == fault_card_id
            assert writer.execute("SELECT COUNT(*) FROM cards WHERE id=? AND content_type='tactic'", (fault_card_id,)).fetchone()[0] == 1
            assert writer.execute("SELECT COUNT(*) FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
                                  ("__captured_tactics__", fault_card_id)).fetchone()[0] == 1
            assert writer.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued' AND attempt_state='guided'",
                                  (fault_card_id,)).fetchone()[0] == 1
            assert writer.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (capture_id,)).fetchone()[0] == 0
            observed_admission.append(True)
            raise RuntimeError("synthetic receipt failure")
        with patch.object(capture_storage, "save_capture_receipt", side_effect=fail_after_admission) as failing_receipt:
            assert execute_command(fault["capture_id"], "tactics.capture.create", {"request": fault}) is None
            assert failing_receipt.call_count == 1
        assert observed_admission == [True]
        assert read_operation(fault["capture_id"])["state"] == "failed"
        assert business_rows() == before_fault
        # Existing-card collision rolls back the repertoire/event/schedule/queue changes.
        with postgres_store.connection() as database:
            database.execute("UPDATE cards SET archived=1 WHERE id=?", (captured["card_id"],))
        collision = {**request, "capture_id": str(uuid.uuid4())}
        assert execute_command(collision["capture_id"], "tactics.capture.create", {"request": collision}) is None
        assert read_operation(collision["capture_id"])["error"]["status_code"] == 409
        with postgres_store.connection() as database:
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (collision["capture_id"],)).fetchone()[0] == 0
            database.execute("UPDATE cards SET archived=0 WHERE id=?", (captured["card_id"],))
            database.execute("UPDATE daily_queue SET status='complete' WHERE card_id=?", (captured["card_id"],))
            database.execute("UPDATE settings SET tactics_new_per_day=0")
        recapture = {**request, "capture_id": str(uuid.uuid4())}
        result = execute_command(recapture["capture_id"], "tactics.capture.create", {"request": recapture})
        assert result["reused"] and not result["introduced"]
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'", (captured["card_id"],)).fetchone()[0] == 1
    finally:
        with postgres_store.connection() as database:
            database.execute("UPDATE settings SET tactics_new_per_day=?", (original_limit,))
            database.execute("DELETE FROM tactic_pack_activation WHERE pack_id='capture-synthetic'")
            # Retain event/card rows for backup comparison, but retire this scenario's
            # queue entries before the independent foreground study scenario begins.
            fixture_card_ids = (card_id(request["starting_fen"], request["moves"]), prepared["card_id"])
            database.execute("UPDATE cards SET archived=1 WHERE id IN (?,?)", fixture_card_ids)
            database.execute("UPDATE daily_queue SET status='complete' WHERE card_id IN (?,?)", fixture_card_ids)
        postgres_store.close_pools()
    print("PASS capture/automatic contention, quota recheck, restart and receipt replay, atomic rejection, and finished queue reopen")


if __name__ == "__main__":
    main()
