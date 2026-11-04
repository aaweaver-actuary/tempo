"""Selected-game command contract and native parity on synthetic rows."""
import json
from types import SimpleNamespace

import pytest

from app.product_composition import game_collection_status as status
from test_game_collection_status import reader_database, stored  # noqa: F401
from test_product_game_lapse_postgres import owned_postgres  # noqa: F401


class SelectedOnly:
    def __init__(self, identity=None, selected=None):
        self.calls = []
        self.identity_result = identity or {"availability": "available", "reader_role": "reader", "transaction_read_only": "on"}
        self.selected_result = selected or {"availability": "available", "provider_game_id": "private-game", "comparison": "unresolved"}

    def __getattr__(self, name):
        raise AssertionError("unexpected operation: " + name)

    def identity(self):
        self.calls.append(("identity",))
        return self.identity_result

    def selected_game(self, workspace, provider, game_id):
        self.calls.append(("selected_game", workspace, provider, game_id))
        return self.selected_result


def test_selected_game_only_calls_exact_ports_and_redacts(monkeypatch, capsys):
    reader = SelectedOnly()
    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: pytest.fail("git probe"))
    monkeypatch.setattr(status, "feedback_release_capabilities", lambda *a: pytest.fail("capabilities"))
    assert status.main(["--selected-game-only", "--workspace", "fixture", "--provider", "chess.com", "--game-id", "private-game"], reader_factory=lambda: reader) == 0
    output = capsys.readouterr().out
    assert output.count("\n") == 1 and output.endswith("\n") and "private-game" not in output
    assert set(json.loads(output)) == {"schema_version", "read_only", "identity", "selected_game"}
    assert reader.calls == [("identity",), ("selected_game", "fixture", "chesscom", "private-game")]


def test_selected_game_only_failed_identity_stops_read(capsys):
    reader = SelectedOnly(identity={"availability": "unavailable", "diagnostic": "collection_read_unavailable"})
    assert status.main(["--selected-game-only", "--provider", "lichess", "--game-id", "g"], reader_factory=lambda: reader) == 2
    assert reader.calls == [("identity",)]
    assert json.loads(capsys.readouterr().out)["selected_game"]["observed"] is False


@pytest.mark.parametrize("private", [False, True])
def test_selected_game_only_overflow_is_bounded(private, capsys):
    reader = SelectedOnly(selected={"availability": "available", "findings": ["x" * status.MAX_OUTPUT_BYTES]})
    args = ["--selected-game-only", "--provider", "lichess", "--game-id", "g"] + (["--private"] if private else [])
    assert status.main(args, reader_factory=lambda: reader) == 2
    raw = capsys.readouterr().out
    assert len(raw.encode()) <= status.MAX_OUTPUT_BYTES and raw.count("\n") == 1
    result = json.loads(raw)
    assert result["truncated"] is True and result["selected_game"] == {"availability": "unavailable", "diagnostic": "output_exceeds_256_kib"}


@pytest.mark.parametrize("args", [[], ["--provider", "lichess"], ["--game-id", "g"], ["--provider", "lichess", "--game-id", "  "], ["--provider", "lichess", "--game-id", "g", "--identity-only"], ["--provider", "lichess", "--game-id", "x" * 201]])
def test_selected_game_only_bad_arguments(args):
    with pytest.raises(SystemExit) as error:
        status.main(["--selected-game-only", *args], reader_factory=lambda: pytest.fail("constructed reader"))
    assert error.value.code == 2


def test_selected_game_only_missing_reader_never_uses_writer(monkeypatch, capsys):
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "must-not-be-used")
    assert status.main(["--selected-game-only", "--provider", "lichess", "--game-id", "g"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["identity"]["diagnostic"] == "games_reader_profile_required"
    assert result["selected_game"]["observed"] is False


def test_selected_game_only_native_matches_full_with_receipt(stored, monkeypatch, capsys):
    from psycopg.types.json import Jsonb
    from test_product_game_review_receipts import scenario
    case = scenario()
    delivery = case.delivery.model_dump(mode="json")
    delivery.update(operation_id=stored.identity, source_key=stored.identity)
    with stored.database.admin() as database:
        database.execute_native("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'encounter','acknowledged',0,%s)", (stored.identity, stored.identity, stored.identity, Jsonb(delivery)))
    stored.reader.receipts = lambda: SimpleNamespace(encounter_status=lambda key: case.receipt, effect_status=lambda key: None)
    args = ["--workspace", stored.identity, "--provider", "chess.com", "--game-id", stored.identity, "--private"]
    assert status.main(args, reader_factory=lambda: stored.reader) == 0
    full = json.loads(capsys.readouterr().out)["selected_game"]
    assert full["receipts"] and full["receipts"][0]["receipt"] is not None
    def forbidden(*args, **kwargs):
        pytest.fail("unrelated reader called")
    for name in ("report", "providers", "collection", "delivery", "managed_mode", "repertoire_readiness"):
        monkeypatch.setattr(stored.reader, name, forbidden, raising=False)
    assert status.main(["--selected-game-only", *args], reader_factory=lambda: stored.reader) == 0
    selected = json.loads(capsys.readouterr().out)["selected_game"]
    assert selected == full
