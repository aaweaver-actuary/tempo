import json

import pytest

from app.product_composition.game_collection_status import MAX_OUTPUT_BYTES, main


class Reader:
    def __init__(self, identity=None, selected=None):
        self.calls = []
        self.identity_value = identity or {"availability": "available", "reader_role": "reader"}
        self.selected_value = selected or {"availability": "available", "comparison": "unresolved"}

    def identity(self):
        self.calls.append("identity")
        return self.identity_value

    def selected_game(self, workspace, provider, game_id):
        self.calls.append((workspace, provider, game_id))
        return self.selected_value

    def __getattr__(self, name):
        raise AssertionError(f"unexpected operation: {name}")


def invoke(reader, capsys, *extra):
    code = main(["--selected-game-only", "--provider", "chess.com", "--game-id", "game", *extra],
                reader_factory=lambda: reader)
    output = capsys.readouterr()
    assert not output.err
    assert len(output.out.splitlines()) == 1
    return code, json.loads(output.out), output.out


def test_selected_game_only_uses_only_identity_and_selected_reader(capsys):
    reader = Reader(selected={"availability": "available", "game_id": "secret", "comparison": "unresolved"})
    code, result, _ = invoke(reader, capsys)
    assert code == 0
    assert reader.calls == ["identity", ("legacy", "chesscom", "game")]
    assert set(result) == {"schema_version", "read_only", "identity", "selected_game"}
    assert result["selected_game"]["game_id"].startswith("redacted:")


def test_selected_game_only_failed_identity_never_reads_game(capsys):
    reader = Reader(identity={"availability": "unavailable", "diagnostic": "collection_read_unavailable"})
    code, result, _ = invoke(reader, capsys)
    assert code == 2 and reader.calls == ["identity"]
    assert result["selected_game"] == {"availability": "unavailable", "observed": False}


def test_selected_game_only_overflow_is_bounded_even_private(capsys):
    reader = Reader(identity={"availability": "available", "extra": "x" * MAX_OUTPUT_BYTES},
                    selected={"availability": "available", "payload": "x" * MAX_OUTPUT_BYTES})
    code, result, output = invoke(reader, capsys, "--private")
    assert code == 2 and len(output.encode()) <= MAX_OUTPUT_BYTES
    assert result["truncated"] is True
    assert result["selected_game"] == {"availability": "unavailable", "diagnostic": "output_exceeds_256_kib"}


@pytest.mark.parametrize("args", [[], ["--provider", "lichess"], ["--provider", "lichess", "--game-id", ""],
                                  ["--provider", "lichess", "--game-id", "x", "--identity-only"]])
def test_selected_game_only_rejects_bad_arguments(args):
    with pytest.raises(SystemExit) as error:
        main(["--selected-game-only", *args], reader_factory=lambda: Reader())
    assert error.value.code == 2


def test_selected_game_only_unavailable_selected_exits_two(capsys):
    reader = Reader(selected={"availability": "unavailable", "diagnostic": "collection_read_unavailable"})
    code, result, _ = invoke(reader, capsys)
    assert code == 2 and result["selected_game"]["availability"] == "unavailable"
    assert len(reader.calls) == 2


def test_selected_game_only_missing_reader_profile_never_uses_writer(monkeypatch, capsys):
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "writer-must-not-be-used")
    code = main(["--selected-game-only", "--provider", "lichess", "--game-id", "game"])
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result["identity"]["diagnostic"] == "games_reader_profile_required"
    assert result["selected_game"]["observed"] is False
