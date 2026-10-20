import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from tempo_game_ingestion import BytesInput, FileInput, PgnGameIngester
from tempo_game_ingestion.pgn import MAX_SOURCE_BYTES, normalize_pgn
from tempo_game_review_contracts import CanonicalGame
from tempo_game_review_contracts.replay import replay_line


def pgn(moves="1. e4 e5 2. Nf3 Nc6 1-0", *, white="Learner", black="Opponent", extra="", result="1-0"):
    return (f'[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n'
            f'{extra}\n{moves}\n').encode()


SCOPE = dict(workspace_id="workspace-a", game_id="game-a", game_revision=2,
             source_id="file-a", source_revision=3, player="Learner")


def ingest(raw, **changes):
    return PgnGameIngester().ingest(BytesInput(raw), **(SCOPE | changes))


def test_explicit_byte_input_emits_canonical_game_and_exact_raw_digest():
    raw = pgn()
    game = ingest(raw)
    assert isinstance(game, CanonicalGame)
    assert game.model_dump() == dict(schema_version=1, workspace_id="workspace-a", game_id="game-a",
        game_revision=2, source_id="file-a", source_revision=3,
        source_digest=hashlib.sha256(raw).hexdigest(),
        starting_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        moves=["e2e4", "e7e5", "g1f3", "b8c6"], learner_color="white", played_at=None)
    assert ingest(raw) == game
    assert ingest(raw + b"\n").source_digest != game.source_digest


def test_input_is_read_once_without_implicit_followup_actions():
    class Source:
        reads = 0
        def read(self):
            self.reads += 1
            return pgn()
    source = Source()
    result = PgnGameIngester().ingest(source, **SCOPE)
    assert source.reads == 1
    assert result.game_revision == 2


def test_file_adapter_is_explicit_and_preserves_input_bytes(tmp_path):
    path = tmp_path / "game.pgn"
    raw = b"\xef\xbb\xbf" + pgn(extra='[Event "Synthetic épreuve"]')
    path.write_bytes(raw)
    before = path.read_bytes()
    game = PgnGameIngester().ingest(FileInput(path), **SCOPE)
    assert game.source_digest == hashlib.sha256(before).hexdigest()
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {path}


def test_file_adapter_bounds_bytes_before_parsing(tmp_path):
    path = tmp_path / "large.pgn"
    path.write_bytes(b" " * (MAX_SOURCE_BYTES + 2))
    assert len(FileInput(path).read()) == MAX_SOURCE_BYTES + 1
    with pytest.raises(ValueError, match="bounded_source"):
        PgnGameIngester().ingest(FileInput(path), **SCOPE)


@pytest.mark.parametrize("field,value", [("workspace_id", ""), ("game_id", ""), ("source_id", ""),
    ("game_revision", 0), ("source_revision", 0), ("game_revision", True)])
def test_invalid_explicit_identity_or_revision_is_rejected(field, value):
    with pytest.raises(ValidationError):
        ingest(pgn(), **{field: value})


@pytest.mark.parametrize("raw,player,reason", [
    (b"", "Learner", "invalid_or_illegal_pgn"),
    (pgn() + pgn(), "Learner", "one_game_per_source"),
    (pgn("1. e5 1-0"), "Learner", "invalid_or_illegal_pgn"),
    (pgn(result="*", moves="1. e4 *"), "Learner", "unfinished_or_unknown_result"),
    (pgn(extra='[Variant "Atomic"]'), "Learner", "unsupported_variant"),
    (pgn(), "", "explicit_player"),
    (pgn(), "Other", "observed_player"),
    (pgn(black="Learner"), "Learner", "observed_player"),
])
def test_strict_pgn_rejections_retain_existing_game_evidence_safeguards(raw, player, reason):
    with pytest.raises(ValueError, match=reason):
        ingest(raw, player=player)


def test_injected_source_must_return_bytes():
    with pytest.raises(ValueError, match="bytes_source"):
        ingest("not bytes")


def test_black_player_identity_is_case_insensitive_and_explicit():
    assert ingest(pgn(white="Opponent", black="LEARNER"), player="learner").learner_color == "black"


def test_from_position_castling_and_shared_replay_preserve_rights():
    raw = pgn("1. O-O O-O-O 1-0", extra='[SetUp "1"]\n[FEN "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"]')
    game = ingest(raw)
    assert game.moves == ["e1g1", "e8c8"]
    positions = replay_line(game.starting_fen, game.moves)
    assert positions[0]["fen"].split()[2] == "KQkq"
    assert positions[-1]["fen"].split()[2] == "-"


def test_shared_replay_preserves_en_passant_target_and_actual_capture():
    game = ingest(pgn("1. e4 a6 2. e5 d5 3. exd6 1-0"))
    positions = replay_line(game.starting_fen, game.moves)
    assert positions[1]["fen"].split()[3] == "e3"
    assert game.moves[-1] == "e5d6"


def test_promotion_is_canonical_uci():
    raw = pgn("1. a8=Q 1-0", extra='[SetUp "1"]\n[FEN "7k/P7/8/8/8/8/8/4K3 w - - 0 1"]')
    assert ingest(raw).moves == ["a7a8q"]


def test_variations_and_comments_do_not_replace_the_played_mainline():
    game = ingest(pgn("1. e4 {played} (1. d4 d5) e5 2. Nf3 1-0"))
    assert game.moves == ["e2e4", "e7e5", "g1f3"]


def test_date_only_never_invents_a_midnight_timestamp():
    assert ingest(pgn(extra='[Date "2026.10.20"]')).played_at is None


def test_known_pgn_time_and_explicit_time_are_normalized_to_utc():
    raw = pgn(extra='[UTCDate "2026.10.20"]\n[UTCTime "18:00:00"]')
    assert ingest(raw).played_at == "2026-10-20T18:00:00+00:00"
    assert ingest(raw, played_at="2026-10-20T21:00:00+02:00").played_at == "2026-10-20T19:00:00+00:00"
    assert normalize_pgn(raw, "Learner", played_at=datetime(2026, 10, 20, tzinfo=timezone.utc))["played_at"] == "2026-10-20T00:00:00+00:00"


def test_naive_or_invalid_explicit_time_is_rejected():
    for value in ("2026-10-20T18:00:00", "not a date"):
        with pytest.raises(ValueError):
            ingest(pgn(), played_at=value)


def test_legacy_normalizer_shape_is_preserved_for_the_compatibility_facade():
    normalized = normalize_pgn(pgn(), "LEARNER")
    assert set(normalized) == {"starting_fen", "moves", "observed_color", "player", "played_at", "source_digest", "headers"}
    assert normalized["player"] == "learner"


def test_contract_ply_bound_rejects_a_long_legal_game():
    # Repeated legal knight moves exercise the PGN path, not a mocked move list.
    import chess
    board = chess.Board()
    tokens = []
    for ply in range(1001):
        move = chess.Move.from_uci(("g1f3", "g8f6", "f3g1", "f6g8")[ply % 4])
        if ply % 2 == 0:
            tokens.append(f"{ply // 2 + 1}.")
        tokens.append(board.san(move))
        board.push(move)
    with pytest.raises(ValueError, match="game_exceeds_contract_move_bound"):
        ingest(pgn(" ".join(tokens) + " 1-0"))


@pytest.mark.parametrize("raw", [b"\xff\xfe", pgn(extra='[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]')])
def test_invalid_encoding_or_starting_position_never_emits_a_game(raw):
    with pytest.raises(ValueError):
        ingest(raw)


def test_input_failure_propagates_without_inventing_a_canonical_game():
    class UnavailableInput:
        def read(self):
            raise OSError("Explicit source unavailable")
    with pytest.raises(OSError, match="Explicit source unavailable"):
        PgnGameIngester().ingest(UnavailableInput(), **SCOPE)
