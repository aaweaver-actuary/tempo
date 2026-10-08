"""Original synthetic regression fixtures for the offline corpus pipeline."""

from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
import signal
import selectors
from pathlib import Path
import sqlite3
import subprocess
import sys

import chess
import chess.pgn
import pytest

from app.services import stalemate_swindles as swindles
from app.services.study_grading import evaluate_answer
from app.services.study_portable import import_bundle, validate_bundle
from app.study_contracts import MoveAnswer
from app.study_migration import STUDY_TABLES


TRAP_FEN = "8/4p3/8/3k4/7R/1q6/8/K7 w - - 0 1"
FORCED_FEN = "8/8/2ppp3/2pkp3/7R/1q6/8/K7 w - - 0 1"
ONLY_MOVE_FEN = "8/8/8/8/7R/1q6/6k1/K6r w - - 0 1"
BLACK_FEN = "k7/8/1Q6/7r/3K4/8/4P3/8 b - - 0 1"
ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/stalemate_swindles.py"


def synthetic_pgn(fen=TRAP_FEN, moves=("h4d4", "d5d4"), **headers):
    game = chess.pgn.Game()
    game.setup(chess.Board(fen))
    game.headers.update({"Event": "Rated Blitz game", "Site": "https://lichess.org/synthet1",
        "White": "Private white account", "Black": "Private black account", "UTCDate": "2026.09.12",
        "Result": "1/2-1/2", "WhiteElo": "1500", "BlackElo": "1600", "TimeControl": "300+3", "Termination": "Normal"})
    game.headers.update(headers)
    current_node = game
    for move in moves:
        current_node = current_node.add_variation(chess.Move.from_uci(move))
    return game.accept(chess.pgn.StringExporter(headers=True, comments=False)) + "\n\n"


def mined(raw_pgn=None, **options):
    counts = swindles.new_counts()
    candidates = list(swindles.mine_candidates(io.StringIO(raw_pgn or synthetic_pgn()), "2026-09", options.pop("filters", swindles.MiningFilters()), counts, **options))
    return candidates, counts


@pytest.mark.parametrize("fen,moves,color", [(TRAP_FEN, ("h4d4", "d5d4"), "white"), (BLACK_FEN, ("h5d5", "d4d5"), "black")])
def test_stalemate_swindle_starts_on_defenders_trap_move_not_opponents_stalemating_blunder(fen, moves, color):
    candidates, _ = mined(synthetic_pgn(fen, moves))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["puzzle_fen"] == fen
    assert candidate["defender_color"] == color
    assert candidate["swindle_move_uci"] == moves[0]
    assert candidate["opponent_reply_uci"] == moves[1]
    terminal = chess.Board(fen)
    for move in moves:
        terminal.push_uci(move)
    assert terminal.is_stalemate() and candidate["terminal_fen"] == terminal.fen()


@pytest.mark.parametrize("moves", [(), ("h4h5",)])
def test_stalemate_swindle_rejects_non_stalemate_draws(moves):
    assert mined(synthetic_pgn(moves=moves))[0] == []


@pytest.mark.parametrize("headers", [{"WhiteTitle": "BOT"}, {"BlackTitle": "BOT"}, {"Event": "Rated Bullet game"}, {"WhiteElo": "999"}, {"BlackElo": "?"}, {"Termination": "Time forfeit"}, {"Variant": "Chess960"}])
def test_stalemate_swindle_default_filters_bots_bullet_low_ratings_and_small_material_deficits(headers):
    assert mined(synthetic_pgn(**headers))[0] == []
    assert mined(filters=swindles.MiningFilters(min_material_deficit=6))[0] == []


def test_stalemate_swindle_configurable_filters_allow_bots_bullet_and_rating_changes():
    candidates, _ = mined(synthetic_pgn(WhiteTitle="BOT", Event="Rated Bullet game", WhiteElo="900"),
        filters=swindles.MiningFilters(min_rating=900, speeds=("bullet",), include_bots=True))
    assert candidates[0]["is_bot"] and candidates[0]["speed"] == "bullet"


def test_stalemate_swindle_excludes_positions_with_only_one_legal_defender_move():
    candidates, counts = mined(synthetic_pgn(ONLY_MOVE_FEN, ("h4h1", "g2h1")))
    assert candidates == [] and counts["excluded_forced_single_move"] == 1


def test_stalemate_swindle_classifies_immediate_forced_stalemate_separately_from_historical_trap():
    trap = mined()[0][0]
    forced = mined(synthetic_pgn(FORCED_FEN))[0][0]
    assert trap["swindle_kind"] == "historical_trap"
    assert trap["opponent_reply_count"] == 5 and trap["stalemating_replies_uci"] == ["d5d4"]
    assert forced["swindle_kind"] == "immediate_forced_stalemate"
    assert forced["opponent_reply_count"] == 3
    assert forced["stalemating_replies_uci"] == ["c5d4", "d5d4", "e5d4"]
    assert "all 3 legal opponent replies" in swindles.exercise_specification(forced).further_analysis
    assert "forces" not in swindles.exercise_specification(trap).prompt


def test_stalemate_swindle_deduplication_is_deterministic_and_not_input_order_dependent():
    candidates = [mined(synthetic_pgn(Site=f"https://lichess.org/synthet{index}", WhiteElo=rating))[0][0] for index, rating in [(1, "1000"), (2, "2000"), (3, "2000")]]
    forward = swindles.select_candidates(candidates)
    assert forward == swindles.select_candidates(reversed(candidates))
    assert forward[0][0]["game_id"] == "synthet2" and forward[1]["unique_candidates"] == 1


def test_stalemate_swindle_selection_balances_motifs_and_preserves_cap_during_shortfall():
    candidates = [mined(synthetic_pgn(fen, moves))[0][0] for fen, moves in [(TRAP_FEN, ("h4d4", "d5d4")), (BLACK_FEN, ("h5d5", "d4d5")), (FORCED_FEN, ("h4d4", "d5d4"))]]
    selected, counts = swindles.select_candidates(candidates, max_per_motif=1)
    assert len(selected) == 2
    assert max(Counter(candidate["motif_signature"] for candidate in selected).values()) == 1
    assert counts["shortfall_insufficient_candidates"] == 297
    assert counts["shortfall_motif_cap"] == 1


def test_stalemate_swindle_identity_ignores_fen_clocks_and_features_describe_material():
    assert swindles.candidate_key(TRAP_FEN, "h4d4") == swindles.candidate_key(TRAP_FEN.replace("0 1", "17 50"), "h4d4")
    for fen, material_class in [("8/8/8/8/8/1q6/2k5/K7 w - - 0 1", "lone_king"),
        ("8/8/8/8/8/1q6/2k5/KP6 w - - 0 1", "pawns_only"),
        ("8/8/8/8/8/1q6/2k5/KN6 w - - 0 1", "trapped_piece"),
        ("8/8/8/8/8/1q6/2k5/KNP5 w - - 0 1", "mixed_residue")]:
        features = swindles.pattern_features(chess.Board(fen), chess.WHITE, "historical_trap")
        assert features["final_material_class"] == material_class
        assert features["king_zone"] == "corner"


def test_stalemate_study_bundle_is_byte_stable_for_identical_candidates():
    candidates = mined()[0] + mined(synthetic_pgn(FORCED_FEN))[0]
    selected = swindles.select_candidates(candidates)[0]
    bundle_bytes = swindles.json_bytes(swindles.build_bundle(selected, "synthetic-v1"))
    assert bundle_bytes == swindles.json_bytes(swindles.build_bundle(swindles.select_candidates(reversed(candidates))[0], "synthetic-v1"))
    assert b"Private white account" not in bundle_bytes and b"Private black account" not in bundle_bytes


def test_stalemate_study_bundle_imports_through_existing_study_contract():
    bundle = swindles.build_bundle(mined()[0], "synthetic-v1")
    validate_bundle(bundle)
    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        for statement in STUDY_TABLES[:7]:
            database.execute(statement)
        result = import_bundle(database, bundle)
        assert database.execute("SELECT title FROM studies WHERE id=?", (result["study_id"],)).fetchone()[0] == "Stalemate Swindles"
        positions = list(database.execute("SELECT * FROM study_positions ORDER BY length(node_path)"))
        assert [json.loads(position["history_json"]) for position in positions] == [[], ["h4d4"], ["h4d4", "d5d4"]]
        assert chess.Board(positions[-1]["fen"]).is_stalemate()
        exercise = database.execute("SELECT * FROM study_exercises").fetchone()
        assert exercise["status"] == "draft" and exercise["current_revision"] == 1 and exercise["point_value"] is None
        specification = json.loads(database.execute("SELECT specification_json FROM study_exercise_revisions").fetchone()[0])
        assert (specification["type"], specification["mode"], specification["grading_policy"]) == ("move_line", "single", "open_judgment")
        assert specification["accepted_lines"] == [["h4d4"]]
        assert all(isinstance(value, str) for value in json.loads(exercise["source_json"]).values())
        assert not database.execute("PRAGMA foreign_key_check").fetchall()
    assert set(bundle["tables"]) == set(swindles.TABLES)


def test_stalemate_swindle_uses_existing_open_judgment_grading():
    candidate = mined()[0][0]
    specification = swindles.exercise_specification(candidate)
    for move, expected in [("h4d4", "correct"), ("h4h5", "unrecognized"), ("a1a8", "invalid_submission")]:
        assert evaluate_answer(specification, MoveAnswer(type="move_line", moves=[move]), candidate["puzzle_fen"])["outcome"] == expected


def test_stalemate_miner_prefilters_non_draws_before_chess_parsing(monkeypatch):
    def unexpected_parse(*_args, **_kwargs):
        raise AssertionError("Rejected headers must not reach the chess parser")
    monkeypatch.setattr(chess.pgn, "read_game", unexpected_parse)
    for headers in [{"Result": "1-0"}, {"WhiteElo": "?"}, {"Event": "Rated Bullet game"}]:
        assert mined(synthetic_pgn(**headers))[0] == []


def test_stalemate_miner_malformed_records_continue_or_fail_strictly_and_resynchronize():
    invalid = synthetic_pgn().replace("1. Rd4+ Kxd4", "1. Rh9 Kxd4")
    candidates, counts = mined(invalid + synthetic_pgn())
    assert len(candidates) == 1 and counts["parse_errors"] == 1
    with pytest.raises(ValueError, match="Malformed game"):
        mined(invalid, strict=True)
    oversized = synthetic_pgn().replace("1. Rd4+", "{" + "x" * 1200 + "}\n1. Rd4+")
    candidates, counts = mined(oversized + synthetic_pgn(), max_record_bytes=1024)
    assert len(candidates) == 1 and counts["parse_errors"] == 1


def test_stalemate_miner_preserves_multiline_comments_crlf_and_final_record_without_blank_line():
    raw_pgn = synthetic_pgn().replace("1. Rd4+", '1. Rd4+ {\n[Event "comment, not a new game"]\n}\n').strip().replace("\n", "\r\n")
    candidates, counts = mined(raw_pgn)
    assert len(candidates) == 1 and counts["games_scanned"] == 1


def run_cli(*arguments, input_text=None, environment=None):
    return subprocess.run([sys.executable, str(CLI), *arguments], input=input_text, text=True, capture_output=True,
                          env=environment, cwd=ROOT, timeout=20)


def test_stalemate_cli_sampling_metadata_progress_and_atomic_failure(tmp_path):
    output = tmp_path / "candidates.jsonl"
    result = run_cli("mine", "--source-month", "2026-09", "--output", str(output), "--max-games", "1", "--progress-every", "1", input_text=synthetic_pgn())
    assert result.returncode == 0, result.stderr
    metadata = json.loads(Path(str(output) + ".metadata.json").read_text())
    assert metadata["completion"] == {"complete": False, "reason": "sampling_mode"}
    assert not metadata["source"]["sha256_verified"] and result.stdout == ""
    assert metadata["candidate_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    prior_output = output.read_bytes()
    failed = run_cli("mine", "--source-month", "2026-09", "--output", str(output), "--strict", input_text=synthetic_pgn().replace("1. Rd4+", "1. Rh9"))
    assert failed.returncode == 1 and output.read_bytes() == prior_output
    assert not list(tmp_path.glob("*.partial"))
    denied_build = run_cli("build", "--candidates", str(output), "--corpus-id", "lichess-standard-2026-09-v1", "--output", str(tmp_path / "bundle.json"), "--manifest", str(tmp_path / "manifest.json"))
    assert denied_build.returncode == 1 and not (tmp_path / "bundle.json").exists()


def test_stalemate_cli_plain_path_builds_manifest_and_rejects_tampered_candidates(tmp_path):
    source = tmp_path / "sample.pgn"
    source.write_text(synthetic_pgn())
    output, bundle, manifest = (tmp_path / name for name in ("candidates.jsonl", "bundle.json", "manifest.json"))
    result = run_cli("mine", "--input", str(source), "--source-month", "2026-09", "--output", str(output))
    assert result.returncode == 0, result.stderr
    build_arguments = ("build", "--candidates", str(output), "--corpus-id", "synthetic-v1", "--output", str(bundle), "--manifest", str(manifest))
    result = run_cli(*build_arguments)
    assert result.returncode == 0, result.stderr
    report = json.loads(manifest.read_text())
    assert report["counts"]["selected_puzzles"] == 1 and not report["complete_verified_source"]
    assert report["bundle_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    before = bundle.read_bytes()
    output.write_text(output.read_text().replace('"material_deficit":5', '"material_deficit":6'))
    assert run_cli(*build_arguments).returncode == 1 and bundle.read_bytes() == before


@pytest.mark.parametrize("duplicate_input", ["same_path", "copied_output"])
@pytest.mark.parametrize("previous_outputs", [False, True])
def test_stalemate_build_rejects_duplicate_verified_candidate_receipts(tmp_path, duplicate_input, previous_outputs):
    original, copied, bundle, manifest = (tmp_path / name for name in ("original.jsonl", "copied.jsonl", "bundle.json", "manifest.json"))
    result = run_cli("mine", "--source-month", "2026-09", "--output", str(original), input_text=synthetic_pgn())
    assert result.returncode == 0, result.stderr
    original_metadata = Path(str(original) + ".metadata.json")
    receipt = json.loads(original_metadata.read_text())
    assert receipt["candidate_sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert receipt["counts"]["games_scanned"] == receipt["counts"]["eligible_candidates"] == 1
    copied.write_bytes(original.read_bytes())
    Path(str(copied) + ".metadata.json").write_bytes(original_metadata.read_bytes())
    build_options = ("--corpus-id", "synthetic-receipt-regression-v1", "--output", str(bundle), "--manifest", str(manifest))
    if previous_outputs:
        result = run_cli("build", "--candidates", str(original), *build_options)
        assert result.returncode == 0, result.stderr
        previous_bytes = (bundle.read_bytes(), manifest.read_bytes())
    repeated = original if duplicate_input == "same_path" else copied
    result = run_cli("build", "--candidates", str(original), str(repeated), *build_options)
    assert result.returncode == 1, "Duplicate receipts must not inflate manifest population counters"
    expected_error = "Candidate inputs must not be repeated" if duplicate_input == "same_path" else "Candidate inputs contain duplicate mined output"
    assert expected_error in result.stderr
    if duplicate_input == "copied_output":
        assert str(copied) in result.stderr
    if previous_outputs:
        assert (bundle.read_bytes(), manifest.read_bytes()) == previous_bytes
    else:
        assert not bundle.exists() and not manifest.exists()
    assert not list(tmp_path.rglob("*.partial"))


@pytest.fixture
def synthetic_receipt_build(tmp_path):
    candidate_path = tmp_path / "candidates.jsonl"
    result = run_cli("mine", "--source-month", "2026-09", "--output", str(candidate_path), input_text=synthetic_pgn())
    assert result.returncode == 0, result.stderr
    metadata_path = Path(str(candidate_path) + ".metadata.json")
    receipt = json.loads(metadata_path.read_text())
    # Synthetic build-input metadata only; real archive certification is covered
    # by test_stalemate_cli_archive_verifies_source_and_rejects_failed_pipeline.
    synthetic_source_hash = hashlib.sha256(synthetic_pgn().encode()).hexdigest()
    receipt["source"].update({"filename": "lichess_db_standard_rated_2026-09.pgn.zst",
        "sha256": synthetic_source_hash, "expected_sha256": synthetic_source_hash, "sha256_verified": True})
    receipt["completion"]["complete"] = True
    metadata_path.write_text(json.dumps(receipt))
    return candidate_path, metadata_path, receipt


@pytest.mark.parametrize("receipt_section,boolean_field", [("completion", "complete"), ("source", "sha256_verified")])
@pytest.mark.parametrize("metadata_case,invalid_value", [
    pytest.param("value", "false", id="string-false"),
    pytest.param("value", "true", id="string-true"),
    pytest.param("value", 0, id="zero"),
    pytest.param("value", 1, id="one"),
    pytest.param("value", None, id="null"),
    pytest.param("value", [], id="array"),
    pytest.param("value", {}, id="object"),
    pytest.param("missing_field", None, id="missing-field"),
    pytest.param("missing_section", None, id="missing-section"),
    pytest.param("section", None, id="null-section"),
    pytest.param("section", [], id="array-section"),
    pytest.param("section", "false", id="string-section"),
    pytest.param("section", 0, id="number-section"),
    pytest.param("section", True, id="boolean-section"),
])
def test_stalemate_load_candidates_rejects_malformed_receipt_booleans(
        synthetic_receipt_build, receipt_section, boolean_field, metadata_case, invalid_value):
    candidate_path, metadata_path, receipt = synthetic_receipt_build
    if metadata_case == "missing_field":
        del receipt[receipt_section][boolean_field]
    elif metadata_case == "missing_section":
        del receipt[receipt_section]
    elif metadata_case == "section":
        receipt[receipt_section] = invalid_value
    else:
        receipt[receipt_section][boolean_field] = invalid_value
    metadata_path.write_text(json.dumps(receipt))
    module_spec = importlib.util.spec_from_file_location("stalemate_receipt_validation", CLI)
    cli_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli_module)
    accepted_receipts, accepted_candidates = [], []
    with pytest.raises(ValueError) as invalid_metadata:
        for candidate in cli_module.load_candidates([str(candidate_path)], accepted_receipts):
            accepted_candidates.append(candidate)
    assert str(metadata_path) in str(invalid_metadata.value)
    assert f"{receipt_section}.{boolean_field}" in str(invalid_metadata.value)
    assert "JSON Boolean (true or false)" in str(invalid_metadata.value)
    assert accepted_receipts == accepted_candidates == []


@pytest.mark.parametrize("receipt_section,boolean_field", [("completion", "complete"), ("source", "sha256_verified")])
@pytest.mark.parametrize("canonical_corpus", [False, True])
@pytest.mark.parametrize("previous_outputs", [False, True])
def test_stalemate_build_rejects_malformed_receipt_booleans_without_publishing(
        tmp_path, synthetic_receipt_build, receipt_section, boolean_field, canonical_corpus, previous_outputs):
    candidate_path, metadata_path, receipt = synthetic_receipt_build
    bundle, manifest = tmp_path / "bundle.json", tmp_path / "manifest.json"
    corpus_id = "lichess-standard-2026-09-v1" if canonical_corpus else "synthetic-receipt-booleans-v1"
    build_arguments = ("build", "--candidates", str(candidate_path), "--corpus-id", corpus_id,
                       "--output", str(bundle), "--manifest", str(manifest))
    if previous_outputs:
        result = run_cli(*build_arguments)
        assert result.returncode == 0, result.stderr
        previous_bytes = (bundle.read_bytes(), manifest.read_bytes())
    receipt[receipt_section][boolean_field] = "false"
    metadata_path.write_text(json.dumps(receipt))
    rejected_metadata_bytes = metadata_path.read_bytes()
    candidate_bytes = candidate_path.read_bytes()
    result = run_cli(*build_arguments)
    assert result.returncode == 1, "Truthy receipt strings must never authorize a build"
    assert str(metadata_path) in result.stderr
    assert f"{receipt_section}.{boolean_field}" in result.stderr
    assert "JSON Boolean (true or false)" in result.stderr
    if previous_outputs:
        assert (bundle.read_bytes(), manifest.read_bytes()) == previous_bytes
    else:
        assert not bundle.exists() and not manifest.exists()
    assert metadata_path.read_bytes() == rejected_metadata_bytes
    assert candidate_path.read_bytes() == candidate_bytes
    assert not list(tmp_path.rglob("*.partial"))


@pytest.mark.parametrize("complete,sha256_verified", [(True, True), (False, True), (True, False), (False, False)])
@pytest.mark.parametrize("canonical_corpus", [False, True])
def test_stalemate_build_certification_requires_literal_boolean_true(
        tmp_path, synthetic_receipt_build, complete, sha256_verified, canonical_corpus):
    candidate_path, metadata_path, receipt = synthetic_receipt_build
    receipt["completion"]["complete"] = complete
    receipt["source"]["sha256_verified"] = sha256_verified
    metadata_path.write_text(json.dumps(receipt))
    original_metadata_bytes, original_candidate_bytes = metadata_path.read_bytes(), candidate_path.read_bytes()
    bundle, manifest = tmp_path / "bundle.json", tmp_path / "manifest.json"
    corpus_id = "lichess-standard-2026-09-v1" if canonical_corpus else "synthetic-receipt-booleans-v1"
    result = run_cli("build", "--candidates", str(candidate_path), "--corpus-id", corpus_id,
                     "--output", str(bundle), "--manifest", str(manifest))
    verified_complete = complete is True and sha256_verified is True
    if canonical_corpus and not verified_complete:
        assert result.returncode == 1
        assert "Canonical corpus requires complete checksum-verified input" in result.stderr
        assert not bundle.exists() and not manifest.exists()
    else:
        assert result.returncode == 0, result.stderr
        report = json.loads(manifest.read_text())
        assert report["complete_verified_source"] is verified_complete
        assert report["source"]["sha256_verified"] is sha256_verified
        assert report["counts"]["selected_puzzles"] == 1
        assert report["bundle_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert metadata_path.read_bytes() == original_metadata_bytes
    assert candidate_path.read_bytes() == original_candidate_bytes
    assert not list(tmp_path.rglob("*.partial"))


def assert_unverifiable_receipts_are_rejected(tmp_path, source_scans, previous_outputs, *, overlapping_candidates):
    candidate_paths = [tmp_path / "first.jsonl", tmp_path / "second.jsonl"]
    receipts = []
    candidate_identities = []
    for candidate_path, pgn in zip(candidate_paths, source_scans):
        result = run_cli("mine", "--source-month", "2026-09", "--output", str(candidate_path), input_text=pgn)
        assert result.returncode == 0, result.stderr
        receipt = json.loads(Path(str(candidate_path) + ".metadata.json").read_text())
        assert receipt["candidate_sha256"] == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        receipts.append(receipt)
        candidate_identities.append({json.loads(line)["candidate_key"] for line in candidate_path.read_text().splitlines()})
    assert receipts[0]["candidate_sha256"] != receipts[1]["candidate_sha256"]
    assert bool(candidate_identities[0] & candidate_identities[1]) == overlapping_candidates
    for field in ("source_month", "source", "filters", "completion"):
        assert receipts[0][field] == receipts[1][field]
    bundle, manifest = tmp_path / "bundle.json", tmp_path / "manifest.json"
    build_options = ("--corpus-id", "synthetic-distinct-receipts-v1", "--output", str(bundle), "--manifest", str(manifest))
    if previous_outputs:
        result = run_cli("build", "--candidates", str(candidate_paths[0]), *build_options)
        assert result.returncode == 0, result.stderr
        previous_bytes = (bundle.read_bytes(), manifest.read_bytes())
    result = run_cli("build", "--candidates", *(str(candidate_path) for candidate_path in candidate_paths),
                     *build_options)
    assert result.returncode == 1, "Different candidate hashes cannot justify adding scan populations"
    assert "disjoint source coverage cannot be verified" in result.stderr
    assert "one mining output" in result.stderr
    if previous_outputs:
        assert (bundle.read_bytes(), manifest.read_bytes()) == previous_bytes
    else:
        assert not bundle.exists() and not manifest.exists()
    assert not list(tmp_path.rglob("*.partial"))
    return receipts


@pytest.mark.parametrize("previous_outputs", [False, True])
def test_stalemate_build_rejects_overlapping_candidate_receipts(tmp_path, previous_outputs):
    first = synthetic_pgn()
    shared = synthetic_pgn(BLACK_FEN, ("h5d5", "d4d5"), Site="https://lichess.org/synthet2")
    last = synthetic_pgn(FORCED_FEN, Site="https://lichess.org/synthet3")
    receipts = assert_unverifiable_receipts_are_rejected(tmp_path, [first + shared, shared + last],
        previous_outputs, overlapping_candidates=True)
    assert all(receipt["counts"]["games_scanned"] == receipt["counts"]["draw_games_parsed"] ==
               receipt["counts"]["eligible_candidates"] == 2 for receipt in receipts)


@pytest.mark.parametrize("previous_outputs", [False, True])
def test_stalemate_build_rejects_overlapping_scans_without_shared_candidates(tmp_path, previous_outputs):
    first = synthetic_pgn()
    shared_non_candidate = synthetic_pgn(moves=("h4h3",), Site="https://lichess.org/synthet2")
    last = synthetic_pgn(BLACK_FEN, ("h5d5", "d4d5"), Site="https://lichess.org/synthet3")
    assert mined(shared_non_candidate)[0] == []
    receipts = assert_unverifiable_receipts_are_rejected(tmp_path, [first + shared_non_candidate, shared_non_candidate + last],
        previous_outputs, overlapping_candidates=False)
    assert all(receipt["counts"]["games_scanned"] == receipt["counts"]["draw_games_parsed"] == 2 and
               receipt["counts"]["eligible_candidates"] == 1 for receipt in receipts)


@pytest.mark.parametrize("previous_outputs", [False, True])
def test_stalemate_build_rejects_distinct_candidate_outputs_without_verifiable_coverage(tmp_path, previous_outputs):
    assert_unverifiable_receipts_are_rejected(tmp_path, [synthetic_pgn(), synthetic_pgn(BLACK_FEN, ("h5d5", "d4d5"))],
        previous_outputs, overlapping_candidates=False)


def test_stalemate_build_single_receipt_preserves_counts_and_bundle_identity(tmp_path):
    source_scan = synthetic_pgn() * 2 + synthetic_pgn(BLACK_FEN, ("h5d5", "d4d5"), Site="https://lichess.org/synthet2") + synthetic_pgn(moves=("h4h3",), Site="https://lichess.org/synthet3")
    candidates, bundle, manifest = (tmp_path / name for name in ("candidates.jsonl", "bundle.json", "manifest.json"))
    result = run_cli("mine", "--source-month", "2026-09", "--output", str(candidates), input_text=source_scan)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(Path(str(candidates) + ".metadata.json").read_text())
    build_arguments = ("build", "--candidates", str(candidates), "--corpus-id", "synthetic-single-receipt-v1",
                       "--output", str(bundle), "--manifest", str(manifest))
    result = run_cli(*build_arguments)
    assert result.returncode == 0, result.stderr
    report = json.loads(manifest.read_text())
    counts = report["counts"]
    for name in swindles.new_counts():
        assert counts[name] == receipt["counts"][name]
    assert counts["games_scanned"] == counts["draw_games_parsed"] == 4
    assert counts["eligible_candidates"] == 3
    assert counts["unique_candidates"] == counts["selected_puzzles"] == 2
    assert len(json.loads(bundle.read_text())["tables"]["study_exercises"]) == 2
    assert report["source"] == receipt["source"] and report["filters"] == receipt["filters"]
    assert not report["complete_verified_source"]
    # Pin the pre-fix single-input bytes, including all manifest semantics.
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == "26845dab8689a9b462a5230376bb1748a8673c25be7533f242d00e93960d2056"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == "ed31c0273915301366a2797c2e344729e3dcd11c55ef5a4cddfdfdfc6d330be3"
    previous_bytes = (bundle.read_bytes(), manifest.read_bytes())
    result = run_cli(*build_arguments)
    assert result.returncode == 0, result.stderr
    assert (bundle.read_bytes(), manifest.read_bytes()) == previous_bytes
    assert not list(tmp_path.rglob("*.partial"))


@pytest.mark.parametrize("bad_checksum,download_failure,decompression_failure", [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_stalemate_cli_archive_verifies_source_and_rejects_failed_pipeline(tmp_path, bad_checksum, download_failure, decompression_failure):
    # Cross multiple pump chunks so certification must include the compressed tail.
    # Non-draw records avoid unnecessary chess replay while preserving real streaming.
    source_bytes = synthetic_pgn().encode() + b'[Event "Unselected game"]\n[Result "1-0"]\n\n1. e4 1-0\n\n' * 45000
    assert len(source_bytes) > 2 * 1024 * 1024
    source = tmp_path / "source"
    source.write_bytes(source_bytes)
    tool_directory = tmp_path / "bin"
    tool_directory.mkdir()
    for command, body in {"curl": f"import sys\nsys.stdout.buffer.write(open({str(source)!r}, 'rb').read())\nsys.exit({int(download_failure)})\n",
                          "zstd": f"import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.exit({int(decompression_failure)})\n"}.items():
        executable = tool_directory / command
        executable.write_text(f"#!{sys.executable}\n{body}")
        executable.chmod(0o755)
    environment = {**os.environ, "PATH": str(tool_directory) + os.pathsep + os.environ["PATH"]}
    output = tmp_path / "candidates.jsonl"
    checksum = "0" * 64 if bad_checksum else hashlib.sha256(source_bytes).hexdigest()
    result = run_cli("mine", "--source-url", "https://database.lichess.org/standard/lichess_db_standard_rated_2026-09.pgn.zst",
        "--source-sha256", checksum, "--source-month", "2026-09", "--output", str(output), environment=environment)
    if bad_checksum or download_failure or decompression_failure:
        assert result.returncode == 1 and not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        metadata = json.loads(Path(str(output) + ".metadata.json").read_text())
        assert metadata["source"]["sha256_verified"] and metadata["completion"]["complete"]
        bundle, manifest = tmp_path / "bundle.json", tmp_path / "manifest.json"
        build_arguments = ("build", "--candidates", str(output), "--corpus-id", "lichess-standard-2026-09-v1",
                           "--output", str(bundle), "--manifest", str(manifest))
        assert run_cli(*build_arguments).returncode == 0
        original_bytes = bundle.read_bytes()
        assert run_cli(*build_arguments).returncode == 0 and bundle.read_bytes() == original_bytes
        changed_build = run_cli(*build_arguments, "--title", "Changed canonical content")
        assert changed_build.returncode == 1 and "new corpus revision" in changed_build.stderr
        assert bundle.read_bytes() == original_bytes


def run_backpressured_archive(tmp_path, downstream_mode, *, downloader_failure=False, sampling=False,
                             cancellation_signal=None, ignore_termination=False, suppress_interruption=False):
    """Use real owned pipes; an undrained producer cannot finish its 32 MiB write."""
    tool_directory = tmp_path / "bin"
    tool_directory.mkdir()
    producer_pid_path = tmp_path / "producer.pid"
    consumer_pid_path = tmp_path / "consumer.pid"
    consumer_ready_path = tmp_path / "consumer-ready"
    os.mkfifo(consumer_ready_path)
    producer_bytes = 1024 * 1024 if downloader_failure else 32 * 1024 * 1024
    producer_handshake = (f"with open({str(consumer_ready_path)!r}, 'rb', buffering=0) as ready:\n    assert ready.read(1) == b'1'\n"
                          if downloader_failure else "")
    producer_body = (
        f"import os, signal\nfrom pathlib import Path\nPath({str(producer_pid_path)!r}).write_text(str(os.getpid()))\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_termination else "") +
        f"{producer_handshake}"
        f"remaining = {producer_bytes}\n"
        "while remaining:\n    remaining -= os.write(1, b'x' * min(65536, remaining))\n"
        f"raise SystemExit({int(downloader_failure)})\n"
    )
    consumer_body = f"import os, signal, sys\nfrom pathlib import Path\nPath({str(consumer_pid_path)!r}).write_text(str(os.getpid()))\n"
    if downstream_mode == "ignore_termination" or ignore_termination:
        consumer_body += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    if downloader_failure:
        consumer_body += f"with open({str(consumer_ready_path)!r}, 'wb', buffering=0) as ready:\n    ready.write(b'1')\nsignal.pause()\n"
    else:
        consumer_body += f"os.read(0, 1)\nsys.stdout.write({(synthetic_pgn() * (2 if sampling else 1))!r})\nsys.stdout.flush()\n"
        if cancellation_signal is not None:
            consumer_body += f"with open({str(consumer_ready_path)!r}, 'wb', buffering=0) as ready:\n    ready.write(b'1')\nsignal.pause()\n"
        elif sampling:
            consumer_body += "signal.pause()\n"
        elif downstream_mode in ("close_input", "ignore_termination"):
            consumer_body += "os.close(0)\nsignal.pause()\n"
        else:
            consumer_body += f"raise SystemExit({7 if downstream_mode == 'exit_failure' else 0})\n"
    for command, body in (("curl", producer_body), ("zstd", consumer_body)):
        executable = tool_directory / command
        executable.write_text(f"#!{sys.executable}\n{body}")
        executable.chmod(0o755)
    environment = {**os.environ, "PATH": str(tool_directory) + os.pathsep + os.environ["PATH"]}
    output = tmp_path / "candidates.jsonl"
    command = [sys.executable, str(CLI), "mine", "--source-url",
               "https://database.lichess.org/standard/lichess_db_standard_rated_2026-09.pgn.zst",
               "--source-sha256", "0" * 64, "--source-month", "2026-09", "--output", str(output)]
    if sampling:
        command += ["--stop-after-candidates", "1"]
    if suppress_interruption:
        interruption_wrapper = tmp_path / "suppressed-interruption.py"
        interruption_wrapper.write_text(f"""import importlib.util
specification = importlib.util.spec_from_file_location('cli', {str(CLI)!r})
cli = importlib.util.module_from_spec(specification)
specification.loader.exec_module(cli)
original_signal = cli.signal.signal
def install_handler(received_signal, handler):
    if callable(handler) and handler.__name__ == 'cancel_command':
        def suppress_exception(signal_number, frame, cancellation_handler=handler):
            try:
                cancellation_handler(signal_number, frame)
            except cli.ArchiveCancellation:
                pass
        handler = suppress_exception
    return original_signal(received_signal, handler)
cli.signal.signal = install_handler
raise SystemExit(cli.main())
""")
        command[1] = str(interruption_wrapper)
    miner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=environment, cwd=ROOT, start_new_session=True)
    try:
        if cancellation_signal is not None:
            readiness_descriptor = os.open(consumer_ready_path, os.O_RDWR | os.O_NONBLOCK)
            try:
                with selectors.DefaultSelector() as readiness:
                    readiness.register(readiness_descriptor, selectors.EVENT_READ)
                    assert readiness.select(timeout=5), "Archive consumer never reached blocked-stream readiness"
                    assert os.read(readiness_descriptor, 1) == b'1'
                miner.send_signal(cancellation_signal)
            finally:
                os.close(readiness_descriptor)
        try:
            _, diagnostics = miner.communicate(timeout=20)
        except subprocess.TimeoutExpired as timeout:
            pytest.fail("Archive pipeline deadlocked with an owned child blocked by pipe backpressure; "
                        f"miner_status={miner.poll()}; diagnostics={timeout.stderr!r}")
        expected_status = 128 + cancellation_signal if cancellation_signal is not None else (0 if sampling else 1)
        assert miner.returncode == expected_status, diagnostics
        if cancellation_signal is not None:
            assert "interrupted; no completed output published" in diagnostics
        elif not sampling:
            assert "Archive pipeline failed" in diagnostics
        for pid_path in (producer_pid_path, consumer_pid_path):
            assert pid_path.exists(), "Both real pipeline stages must have started"
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)
        if sampling:
            metadata = json.loads(Path(str(output) + ".metadata.json").read_text())
            assert metadata["completion"] == {"complete": False, "reason": "sampling_mode"}
            assert not metadata["source"]["sha256_verified"]
            assert metadata["candidate_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        else:
            assert not output.exists() and not Path(str(output) + ".metadata.json").exists()
        assert not list(tmp_path.glob("*.partial"))
    finally:
        if miner.poll() is None:
            # Let ArchiveStream's context exit reap its own children on the red baseline.
            miner.send_signal(signal.SIGINT)
            try:
                miner.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                pass
        # Target only fixture-owned children. Killing them first lets a live
        # miner reap its children; never signal a process group.
        for pid_path in (producer_pid_path, consumer_pid_path):
            if pid_path.exists():
                try:
                    os.kill(int(pid_path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if miner.poll() is None:
            try:
                miner.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                miner.kill()
                miner.communicate(timeout=5)


@pytest.mark.parametrize("downstream_mode", ["exit_failure", "close_input", "exit_success", "ignore_termination"])
def test_stalemate_archive_decompressor_failure_terminates_upstream_producer_under_backpressure(tmp_path, downstream_mode):
    run_backpressured_archive(tmp_path, downstream_mode)


def test_stalemate_archive_downloader_failure_terminates_blocked_decompressor(tmp_path):
    run_backpressured_archive(tmp_path, "blocked", downloader_failure=True)


def test_stalemate_archive_sampling_cancels_owned_pipeline_without_certifying_checksum(tmp_path):
    run_backpressured_archive(tmp_path, "sampling", sampling=True)


@pytest.mark.parametrize("cancellation_signal", [signal.SIGINT, signal.SIGTERM], ids=["ctrl_c", "term"])
@pytest.mark.parametrize("ignore_termination", [False, True], ids=["ordinary_children", "term_resistant_children"])
def test_stalemate_archive_external_cancellation_reaps_owned_children(tmp_path, cancellation_signal, ignore_termination):
    run_backpressured_archive(tmp_path, "cancellation", cancellation_signal=cancellation_signal,
                             ignore_termination=ignore_termination)


def test_stalemate_archive_cancellation_reaps_children_when_interruption_is_suppressed(tmp_path):
    # An asynchronous exception can be swallowed by an unraisable callback.
    # Cancellation must still wake a blocked read and reap the owned pipeline.
    run_backpressured_archive(tmp_path, "cancellation", cancellation_signal=signal.SIGTERM,
                             ignore_termination=True, suppress_interruption=True)


def test_stalemate_archive_workers_reserve_cancellation_signals_for_main_thread(monkeypatch):
    module_spec = importlib.util.spec_from_file_location("stalemate_cli_worker_signals", CLI)
    cli_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli_module)
    original_popen = cli_module.subprocess.Popen
    original_start = cli_module.threading.Thread.start
    worker_masks = []
    main_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])

    def record_worker_mask(worker):
        original_target = worker._target
        def run_with_recorded_mask(*arguments, **keywords):
            worker_masks.append(signal.pthread_sigmask(signal.SIG_BLOCK, []))
            return original_target(*arguments, **keywords)
        worker._target = run_with_recorded_mask
        original_start(worker)

    def start_fixture_stage(command, **options):
        stage_program = ("import sys; sys.stdout.buffer.write(b'archive')" if command[0] == "curl"
                         else "import sys; sys.stdin.buffer.read()")
        return original_popen([sys.executable, "-c", stage_program], **options)

    monkeypatch.setattr(cli_module.threading.Thread, "start", record_worker_mask)
    monkeypatch.setattr(cli_module.subprocess, "Popen", start_fixture_stage)
    with cli_module.ArchiveStream("fixture", hashlib.sha256(b"archive").hexdigest()) as archive:
        assert archive.text_stream.read() == ""
        archive.verify_complete()
    assert len(worker_masks) == 3
    assert all({signal.SIGINT, signal.SIGTERM} <= worker_mask for worker_mask in worker_masks)
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == main_mask


@pytest.mark.parametrize("received_signal", [signal.SIGINT, signal.SIGTERM])
def test_stalemate_cli_cancellation_restores_signal_handlers_and_defers_repeated_signals(monkeypatch, received_signal):
    module_spec = importlib.util.spec_from_file_location("stalemate_cli_cancellation", CLI)
    cli_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli_module)
    previous_handlers = {signal.SIGINT: object(), signal.SIGTERM: object()}
    installed_handlers = dict(previous_handlers)
    monkeypatch.setattr(cli_module.signal, "getsignal", installed_handlers.__getitem__)
    monkeypatch.setattr(cli_module.signal, "signal", installed_handlers.__setitem__)
    with pytest.raises(cli_module.ArchiveCancellation) as final_interruption:
        with cli_module.cli_cancellation_signals() as cancellation:
            with pytest.raises(cli_module.ArchiveCancellation) as interruption:
                installed_handlers[received_signal](received_signal, None)
            assert interruption.value.received_signal == received_signal
            assert cancellation.received_signal == received_signal
            for deferred_signal in previous_handlers:
                installed_handlers[deferred_signal](deferred_signal, None)
            assert cancellation.received_signal == received_signal
    assert final_interruption.value.received_signal == received_signal
    assert installed_handlers == previous_handlers
    with cli_module.cli_cancellation_signals():
        assert all(callable(handler) for handler in installed_handlers.values())
    assert installed_handlers == previous_handlers


@pytest.mark.parametrize("proof_name", [
    "test_postgres_stalemate_bundle_import_reimport_preserves_draft_content",
    "test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content",
])
@pytest.mark.parametrize("previous_configuration", [None, "postgresql://previous-proof.invalid/unused"])
def test_stalemate_postgres_proofs_configure_and_restore_database_environment(monkeypatch, proof_name, previous_configuration):
    specification = importlib.util.spec_from_file_location("stalemate_postgres_proof", ROOT / "scripts/check_postgres_stalemate_swindles.py")
    proof_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof_module)
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    database_variables = ("TEMPO_DATABASE_WRITE_URL", "TEMPO_DATABASE_READ_URL")
    for variable in database_variables:
        if previous_configuration is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, previous_configuration)
    pool_closures = []
    monkeypatch.setattr(proof_module.postgres_store, "close_pools", lambda: pool_closures.append(True))

    @contextmanager
    def failed_database_operation():
        assert pool_closures, "Pools must reset before the proof opens a connection"
        assert all(os.getenv(variable) == "postgresql://postgres@postgres:5432/tempo" for variable in database_variables)
        raise RuntimeError("database proof sentinel")
        yield  # pragma: no cover - context manager deliberately fails on entry

    monkeypatch.setattr(proof_module.postgres_store, "connection", failed_database_operation)
    with pytest.raises(RuntimeError, match="database proof sentinel"):
        getattr(proof_module, proof_name)()
    assert all(os.getenv(variable) == previous_configuration for variable in database_variables)
    assert len(pool_closures) >= 2, "Pools must also close after failed proof work"


def test_stalemate_postgres_configuration_closes_pools_after_success_and_rejects_live_instances(monkeypatch):
    specification = importlib.util.spec_from_file_location("stalemate_postgres_context", ROOT / "scripts/check_postgres_stalemate_swindles.py")
    proof_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof_module)
    pool_closures = []
    monkeypatch.setattr(proof_module.postgres_store, "close_pools", lambda: pool_closures.append(True))
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    with proof_module.disposable_postgres_configuration():
        assert len(pool_closures) == 1
        assert os.environ["TEMPO_DATABASE_WRITE_URL"] == os.environ["TEMPO_DATABASE_READ_URL"] == "postgresql://postgres@postgres:5432/tempo"
    assert len(pool_closures) == 2
    assert "TEMPO_DATABASE_WRITE_URL" not in os.environ and "TEMPO_DATABASE_READ_URL" not in os.environ
    monkeypatch.delenv("TEMPO_TEST_INSTANCE")
    with pytest.raises(RuntimeError, match="disposable PostgreSQL runner"):
        with proof_module.disposable_postgres_configuration():
            pytest.fail("A live instance must never reach proof work")
    assert len(pool_closures) == 2


@pytest.mark.parametrize("extra_arguments", [
    ("--source-url", "https://database.lichess.org/standard/lichess_db_standard_rated_2026-09.pgn.zst"),
    ("--source-sha256", "0" * 64),
    ("--source-url", "https://database.lichess.org/standard/lichess_db_standard_rated_2026-08.pgn.zst", "--source-sha256", "0" * 64),
])
def test_stalemate_cli_rejects_unpaired_or_wrong_month_archive_options(tmp_path, extra_arguments):
    output = tmp_path / "candidates.jsonl"
    result = run_cli("mine", "--source-month", "2026-09", "--output", str(output), *extra_arguments)
    assert result.returncode == 1 and not output.exists()
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("failure_stage", ["backup", "commit_marker"])
def test_stalemate_output_publication_restores_previous_pair_after_io_failure(tmp_path, monkeypatch, failure_stage):
    module_spec = importlib.util.spec_from_file_location("stalemate_cli", CLI)
    cli_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli_module)
    outputs = []
    for name in ("payload", "marker"):
        destination, staged = tmp_path / name, tmp_path / (name + ".new")
        destination.write_text("previous " + name)
        staged.write_text("new " + name)
        outputs.append((staged, destination))
    if failure_stage == "backup":
        def fail_backup(*_):
            raise OSError("backup disk error")
        monkeypatch.setattr(cli_module.shutil, "copyfile", fail_backup)
    else:
        original_replace = os.replace
        def fail_marker(source, destination):
            if source == outputs[1][0]:
                raise OSError("marker disk error")
            return original_replace(source, destination)
        monkeypatch.setattr(cli_module.os, "replace", fail_marker)
    with pytest.raises(OSError, match="disk error"):
        cli_module.publish_files(outputs)
    assert [destination.read_text() for _, destination in outputs] == ["previous payload", "previous marker"]
    assert not list(tmp_path.glob("*.partial"))


def test_stalemate_build_rejects_chess_metadata_drift():
    candidate = deepcopy(mined()[0][0])
    candidate["stalemating_reply_count"] = 9
    with pytest.raises(ValueError, match="disagrees"):
        swindles.select_candidates([candidate])


def test_checked_in_stalemate_corpora_have_unique_legal_single_moves_and_verified_terminal_sources():
    # No raw-source scan in CI; validate every real artifact present in the checkout.
    for bundle_path in sorted((ROOT / "public/data/studies").glob("stalemate-swindles-*.tempo-study.json")):
        bundle = json.loads(bundle_path.read_text())
        manifest = json.loads(bundle_path.with_name(bundle_path.name.replace(".tempo-study.json", ".manifest.json")).read_text())
        tables = validate_bundle(bundle)
        assert manifest["complete_verified_source"]
        assert manifest["source"]["sha256"] == manifest["source"]["expected_sha256"]
        assert manifest["bundle_sha256"] == hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        assert len(tables["study_exercises"]) == manifest["counts"]["selected_puzzles"]
        assert len(tables["study_exercises"]) == 300 or manifest["counts"]["shortfall_insufficient_candidates"] + manifest["counts"]["shortfall_motif_cap"] == 300 - len(tables["study_exercises"])
        positions = {row["id"]: row for row in tables["study_positions"]}
        revisions = {row["exercise_id"]: json.loads(row["specification_json"]) for row in tables["study_exercise_revisions"]}
        identities = set()
        candidate_keys = set()
        motif_counts = Counter(exercise["sibling_group"] for exercise in tables["study_exercises"])
        assert max(motif_counts.values()) <= manifest["selection"]["max_per_motif"]
        for exercise in tables["study_exercises"]:
            metadata = json.loads(exercise["source_json"])
            specification = revisions[exercise["id"]]
            assert metadata["kind"] == "stalemate_swindle_v1"
            assert all(isinstance(value, str) for value in metadata.values())
            assert exercise["status"] == "draft" and exercise["current_revision"] == 1 and exercise["point_value"] is None
            assert specification["type"] == "move_line" and specification["mode"] == "single" and specification["grading_policy"] == "open_judgment"
            assert len(specification["accepted_lines"]) == len(specification["accepted_lines"][0]) == 1
            root = positions[exercise["position_id"]]
            identity = (" ".join(root["fen"].split()[:4]), specification["accepted_lines"][0][0])
            assert identity not in identities and metadata["candidate_key"] not in candidate_keys
            identities.add(identity)
            candidate_keys.add(metadata["candidate_key"])
            assert metadata["candidate_key"] == swindles.candidate_key(root["fen"], identity[1])
            source_nodes = sorted((row for row in positions.values() if row["source_id"] == root["source_id"]), key=lambda row: len(row["node_path"]))
            assert len(source_nodes) == 3 and source_nodes[1]["move_uci"] == identity[1]
            assert source_nodes[2]["move_uci"] == metadata["opponent_reply_uci"]
            assert chess.Board(source_nodes[2]["fen"]).is_stalemate()
            after_swindle = chess.Board(root["fen"])
            assert ("white" if after_swindle.turn else "black") == metadata["defender_color"]
            assert after_swindle.legal_moves.count() >= 2
            after_swindle.push_uci(identity[1])
            assert after_swindle.fen() == source_nodes[1]["fen"]
            historical_reply = chess.Move.from_uci(metadata["opponent_reply_uci"])
            assert historical_reply in after_swindle.legal_moves
            after_swindle.push(historical_reply)
            assert after_swindle.fen() == source_nodes[2]["fen"] == metadata["terminal_fen"]
            assert after_swindle.is_stalemate()
            after_swindle.pop()
            legal_replies = list(after_swindle.legal_moves)
            terminal_reply_count = 0
            for reply in legal_replies:
                after_swindle.push(reply)
                terminal_reply_count += after_swindle.is_stalemate()
                after_swindle.pop()
            assert len(legal_replies) == int(metadata["opponent_reply_count"])
            assert terminal_reply_count == int(metadata["stalemating_reply_count"])
            assert (terminal_reply_count == len(legal_replies)) == (metadata["swindle_kind"] == "immediate_forced_stalemate")
