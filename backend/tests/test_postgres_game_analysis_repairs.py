"""Legacy Stockfish repairs remain one-row, receipt-backed PostgreSQL commands."""

from app import command_dispatch, game_analysis_commands, main, postgres_store


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Database:
    def __init__(self, selected):
        self.selected = selected
        self.statements = []

    def execute_native(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        return Cursor(self.selected if statement.startswith("SELECT") else None)


def test_postgres_timeout_repair_requeues_one_locked_game_and_is_idle_safe():
    database = Database({"game_id": "game-one", "last_error": "Stockfish took too long"})
    assert game_analysis_commands.repair_one_stockfish_timeout(database, {}) == {
        "requeued": True, "game_id": "game-one",
    }
    assert "LIMIT 1 FOR UPDATE SKIP LOCKED" in database.statements[0][0]
    assert "legacy:game-one" in database.statements[1][1]
    assert "analysis_state='pending'" in database.statements[3][0]
    assert game_analysis_commands.repair_one_stockfish_timeout(Database(None), {}) == {
        "requeued": False,
    }


def test_postgres_network_repair_advances_generation_and_records_provenance():
    database = Database({"game_id": "game-two", "analysis_version": 2,
                         "published_version": 5})
    assert game_analysis_commands.repair_one_legacy_network_identity(database, {}) == {
        "requeued": True, "game_id": "game-two",
    }
    assert "LIMIT 1 FOR UPDATE OF j SKIP LOCKED" in database.statements[0][0]
    assert database.statements[1][1][0] == 6
    assert "legacy-network:game-two" in database.statements[3][1]
    assert game_analysis_commands.repair_one_legacy_network_identity(Database(None), {}) == {
        "requeued": False,
    }


def test_postgres_game_repairs_reuse_engine_operation_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key, background=False:
                        calls.append((name, payload, idempotency_key, background))
                        or {"requeued": False})
    assert main.repair_one_stockfish_timeout("docker", "timeout-one") == {"requeued": False}
    assert main.repair_one_legacy_network_identity("docker", "network-one") == {
        "requeued": False,
    }
    assert calls == [
        ("games.analysis.repair_timeout", {}, "timeout-one", True),
        ("games.analysis.repair_provenance", {}, "network-one", True),
    ]
