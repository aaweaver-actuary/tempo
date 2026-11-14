"""Read-only verification of the checked-in historical Study corpus artifacts."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import chess
from pydantic import TypeAdapter

from app.contracts.study_exercises import ExerciseSpecification
from app.study.policy.study_grading import validate_exercise

ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "studies",
    "study_chapters",
    "study_sources",
    "study_positions",
    "study_exercises",
    "study_exercise_revisions",
    "study_links",
)
SPECIFICATION = TypeAdapter(ExerciseSpecification)


def _historical_candidate_key(puzzle_fen: str, swindle_move_uci: str) -> str:
    canonical_fen = " ".join(chess.Board(puzzle_fen).fen().split()[:4])
    return hashlib.sha256(f"{canonical_fen}\n{swindle_move_uci}".encode()).hexdigest()


def validate_bundle(bundle: object) -> dict[str, list[dict]]:
    if (
        not isinstance(bundle, dict)
        or bundle.get("format") != "tempo-study"
        or bundle.get("schema_version") != 1
    ):
        raise ValueError("Unsupported Tempo study format or major version")
    tables = bundle.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError("Study bundle table set is incomplete")
    if any(
        not isinstance(tables[name], list) or len(tables[name]) > 20_000
        for name in TABLES
    ):
        raise ValueError("Study bundle exceeds its table limits")
    if len(tables["studies"]) != 1:
        raise ValueError("A content bundle must contain one study")
    ids = {}
    for name in TABLES:
        rows = tables[name]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {name} row")
        key = "exercise_id" if name == "study_exercise_revisions" else "id"
        identities = [
            (row.get(key), row.get("revision"))
            if name == "study_exercise_revisions"
            else row.get(key)
            for row in rows
        ]
        if any(
            not identity or (isinstance(identity, tuple) and not identity[0])
            for identity in identities
        ) or len(set(identities)) != len(identities):
            raise ValueError(f"Duplicate or missing {name} IDs")
        ids[name] = {row[key] for row in rows}
    study_id = tables["studies"][0]["id"]
    if any(
        row.get("study_id") != study_id
        for row in tables["study_chapters"]
        + tables["study_exercises"]
        + tables["study_links"]
    ):
        raise ValueError("Bundle references another study")
    if any(
        row.get("chapter_id") not in ids["study_chapters"]
        for row in tables["study_sources"]
    ):
        raise ValueError("Source references a missing chapter")
    positions = {row["id"]: row for row in tables["study_positions"]}
    for row in positions.values():
        if row.get("source_id") not in ids["study_sources"]:
            raise ValueError("Position references a missing source")
        parent = positions.get(row.get("parent_id"))
        if row.get("parent_id") and (
            not parent or parent["source_id"] != row["source_id"]
        ):
            raise ValueError("Position parent is missing or in another tree")
        try:
            board = chess.Board(row["fen"])
            if not board.is_valid():
                raise ValueError("Invalid FEN")
            if parent:
                parent_board = chess.Board(parent["fen"])
                move = chess.Move.from_uci(row["move_uci"])
                if move not in parent_board.legal_moves:
                    raise ValueError("Illegal move edge")
                parent_board.push(move)
                if parent_board.fen() != board.fen():
                    raise ValueError("Move edge FEN mismatch")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid position {row.get('id')}: {error}") from error
    revisions = {}
    for row in tables["study_exercise_revisions"]:
        if row.get("exercise_id") not in ids["study_exercises"]:
            raise ValueError("Revision references a missing exercise")
        revisions.setdefault(row["exercise_id"], set()).add(row["revision"])
        exercise = next(
            item
            for item in tables["study_exercises"]
            if item["id"] == row["exercise_id"]
        )
        position = positions.get(exercise.get("position_id"))
        if not position:
            raise ValueError("Exercise references a missing position")
        try:
            specification = SPECIFICATION.validate_python(
                json.loads(row["specification_json"])
            )
            validate_exercise(specification, position["fen"])
        except (ValueError, TypeError) as error:
            raise ValueError(f"Invalid exercise rubric: {error}") from error
    for exercise in tables["study_exercises"]:
        if exercise.get("current_revision") not in revisions.get(exercise["id"], set()):
            raise ValueError("Exercise current revision is missing")
    for link in tables["study_links"]:
        if link.get("source_position_id") not in positions:
            raise ValueError("Link source is missing")
        if bool(link.get("target_position_id")) == bool(link.get("target_exercise_id")):
            raise ValueError("Link must have one target")
        if (
            link.get("target_position_id")
            and link["target_position_id"] not in positions
        ):
            raise ValueError("Link target position is missing")
        if (
            link.get("target_exercise_id")
            and link["target_exercise_id"] not in ids["study_exercises"]
        ):
            raise ValueError("Link target exercise is missing")
    return tables


def test_checked_in_stalemate_corpora_have_unique_legal_single_moves_and_verified_terminal_sources():
    # No raw-source scan in CI; validate every real artifact present in the checkout.
    corpus_paths = sorted(
        (ROOT / "public/data/studies").glob("stalemate-swindles-*.tempo-study.json")
    )
    assert corpus_paths, "The real checked-in corpora must remain present"
    for bundle_path in corpus_paths:
        bundle = json.loads(bundle_path.read_text())
        manifest = json.loads(
            bundle_path.with_name(
                bundle_path.name.replace(".tempo-study.json", ".manifest.json")
            ).read_text()
        )
        tables = validate_bundle(bundle)
        assert manifest["complete_verified_source"]
        assert manifest["source"]["sha256"] == manifest["source"]["expected_sha256"]
        assert (
            manifest["bundle_sha256"]
            == hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        )
        assert len(tables["study_exercises"]) == manifest["counts"]["selected_puzzles"]
        assert len(tables["study_exercises"]) == 300 or manifest["counts"][
            "shortfall_insufficient_candidates"
        ] + manifest["counts"]["shortfall_motif_cap"] == 300 - len(
            tables["study_exercises"]
        )
        positions = {row["id"]: row for row in tables["study_positions"]}
        revisions = {
            row["exercise_id"]: json.loads(row["specification_json"])
            for row in tables["study_exercise_revisions"]
        }
        identities = set()
        candidate_keys = set()
        motif_counts = Counter(
            exercise["sibling_group"] for exercise in tables["study_exercises"]
        )
        assert max(motif_counts.values()) <= manifest["selection"]["max_per_motif"]
        for exercise in tables["study_exercises"]:
            metadata = json.loads(exercise["source_json"])
            specification = revisions[exercise["id"]]
            assert metadata["kind"] == "stalemate_swindle_v1"
            assert all(isinstance(value, str) for value in metadata.values())
            assert (
                exercise["status"] == "draft"
                and exercise["current_revision"] == 1
                and exercise["point_value"] is None
            )
            assert (
                specification["type"] == "move_line"
                and specification["mode"] == "single"
                and specification["grading_policy"] == "open_judgment"
            )
            assert (
                len(specification["accepted_lines"])
                == len(specification["accepted_lines"][0])
                == 1
            )
            root = positions[exercise["position_id"]]
            identity = (
                " ".join(root["fen"].split()[:4]),
                specification["accepted_lines"][0][0],
            )
            assert (
                identity not in identities
                and metadata["candidate_key"] not in candidate_keys
            )
            identities.add(identity)
            candidate_keys.add(metadata["candidate_key"])
            assert metadata["candidate_key"] == _historical_candidate_key(
                root["fen"], identity[1]
            )
            source_nodes = sorted(
                (
                    row
                    for row in positions.values()
                    if row["source_id"] == root["source_id"]
                ),
                key=lambda row: len(row["node_path"]),
            )
            assert len(source_nodes) == 3 and source_nodes[1]["move_uci"] == identity[1]
            assert source_nodes[2]["move_uci"] == metadata["opponent_reply_uci"]
            assert chess.Board(source_nodes[2]["fen"]).is_stalemate()
            after_swindle = chess.Board(root["fen"])
            assert ("white" if after_swindle.turn else "black") == metadata[
                "defender_color"
            ]
            assert after_swindle.legal_moves.count() >= 2
            after_swindle.push_uci(identity[1])
            assert after_swindle.fen() == source_nodes[1]["fen"]
            historical_reply = chess.Move.from_uci(metadata["opponent_reply_uci"])
            assert historical_reply in after_swindle.legal_moves
            after_swindle.push(historical_reply)
            assert (
                after_swindle.fen()
                == source_nodes[2]["fen"]
                == metadata["terminal_fen"]
            )
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
            assert (terminal_reply_count == len(legal_replies)) == (
                metadata["swindle_kind"] == "immediate_forced_stalemate"
            )
