"""Production response producers against a private SQLite fixture, without lifespan/services.

The browser runner does not execute Python. This separately verifies fixtures; it is
not PostgreSQL persistence/issuance proof. Never reads the user's database.
"""
import json
import sqlite3
from contextlib import ExitStack, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.encoders import jsonable_encoder
from tempo_legacy_database import connections as database
from app import main, command_gateway
from tempo_legacy_database.adapters import postgres_store
from app.contracts.transport.study import ReviewRequest


class FixtureDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 18)


class FixtureDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 18, 16, tzinfo=timezone.utc)


def produce_examples():
    if postgres_store.configured():
        raise RuntimeError("Provider fixtures require an environment without PostgreSQL configuration")
    with TemporaryDirectory(prefix="tempo-b01-provider-") as temporary_directory, ExitStack() as stack:
        stack.enter_context(patch.object(database, "DB_PATH", Path(temporary_directory) / "provider.db"))
        stack.enter_context(patch.object(main, "date", FixtureDate))
        # A serializer contract must never contact a service or start application workers.
        stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Provider fixture attempted network access")))
        database.initialize()
        with database.connection() as connection:
            connection.execute("UPDATE settings SET initial_depth=6,new_cards_per_day=10,timezone='America/New_York'")
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('visual-repertoire','Spanish opening — tournament preparation','Spanish.pgn','2026-09-18T12:00:00Z')")
            moves = json.dumps(["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"])
            starting_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color,first_correct_at) VALUES('visual-card','visual-repertoire','prefix',?,?,'learning','2026-09-18','opening','white','2026-09-17T12:00:00Z')", (starting_fen, moves))
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('visual-repertoire','visual-card')")
            connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('visual-line','visual-repertoire','Main line','white',?,?,'2026-09-18T12:00:00Z')", (starting_fen, moves))
            connection.execute("INSERT INTO daily_queue(id,queue_date,card_id,position) VALUES(1,'2026-09-18','visual-card',0)")
            connection.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=1")
        from app.services import tactical_catalog
        source_catalog = tactical_catalog.catalog()
        selected_packs = source_catalog["packs"][:4]
        selected_themes = {pack["theme"] for pack in selected_packs}
        catalog_input = {**source_catalog, "packs": selected_packs,
                         "themes": [theme for theme in source_catalog["themes"] if theme["id"] in selected_themes]}
        # Bound real catalog inputs before invoking the actual serializer, avoiding
        # reads of hundreds of unrelated pack files just to truncate its output.
        with patch.object(tactical_catalog, "catalog", return_value=catalog_input):
            catalog = main.tactics_catalog()
        activity = main.system_activity(offset=0, limit=50)
        activity.pop("writer", None)  # Writer health is not a frontend-host claim.
        from app.services import background_diagnostics
        with patch.object(background_diagnostics, "datetime", FixtureDateTime), patch.object(background_diagnostics.time, "monotonic", return_value=0), patch.object(background_diagnostics, "read_connection", side_effect=sqlite3.OperationalError("Controlled unavailable storage")):
            diagnostics = main.system_background_diagnostics().model_dump(mode="json")
        return {
            "/api/system/background-diagnostics": diagnostics,
            "/api/settings": main.get_settings().model_dump(mode="json"),
            "/api/queue/window": main.queue_window(limit=20),
            "/api/repertoires": main.list_repertoires(),
            "/api/repertoire/lines": main.repertoire_lines(),
            "/api/progress": main.progress_summary(),
            "/api/tactics/catalog": catalog,
            "/api/tactics/progress": main.tactic_progress(),
            "/api/system/activity": activity,
            "/api/games/sync/status": main.sync_status(),
            "/api/discoveries": main.discoveries_feed(offset=0, limit=100),
            "/api/games/summary": main.summary(),
            "/api/game-insights/motifs": main.game_motif_insights(),
            "/api/cards/visual-card/teaching": main.list_teaching_states("visual-card"),
            "/api/system/browser-activity": main.record_browser_activity(),
        }


def produce_receipts():
    class ReceiptConnection:
        raw = None
        def __init__(self, row):
            self.row = row
            self.raw = self
        def execute(self, statement, parameters):
            assert "FROM operation_receipts" in statement
            assert parameters == ("review-attempt:fixture-original",)
            return self
        def fetchone(self):
            return self.row

    examples = {}
    for state in ("queued", "executing", "retrying", "pending", "blocked", "failed", "complete", "unknown"):
        result = {"persisted": True, "review_id": 88} if state == "complete" else None
        failure = {"status_code": 409, "code": "queue_attempt_retired", "retryable": False,
                   "message": "Original entry retired"} if state == "failed" else None
        row = ("cards.review", "fixture-hash", state, json.dumps(result) if result else None,
               json.dumps(failure) if failure else None, 1, None, None, None, "{}", 0, 1)
        with patch.object(postgres_store, "connection", return_value=nullcontext(ReceiptConnection(None if state == "unknown" else row))):
            examples[state] = command_gateway.read_operation("review-attempt:fixture-original")
    examples["review_request"] = ReviewRequest(outcome="correct", queue_entry_id=41,
        attempt_id="fixture-original", recorded_at="2026-09-18T16:00:00Z").durable_payload()
    return examples


if __name__ == "__main__":
    import sys
    arguments = sys.argv[1:]
    if arguments == ["--all"]:
        value = {"startup": produce_examples(), "receipts": produce_receipts()}
    elif arguments == ["--receipts"]:
        value = produce_receipts()
    elif not arguments:
        value = produce_examples()
    else:
        raise SystemExit("Usage: generate.py [--all|--receipts]")
    print(json.dumps(jsonable_encoder(value), sort_keys=True, ensure_ascii=False, indent=2))
