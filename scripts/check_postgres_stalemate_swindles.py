"""Generated portable-content import proof, only in the disposable durability stack."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys

import chess
import chess.pgn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store
from app.services.stalemate_swindles import MiningFilters, build_bundle, mine_candidates, new_counts
from app.services.study_portable import import_bundle


def test_postgres_stalemate_bundle_import_reimport_preserves_draft_content():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Stalemate import proof requires the disposable PostgreSQL runner")
    dsn = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_WRITE_URL"] = dsn
    os.environ["TEMPO_DATABASE_READ_URL"] = dsn
    postgres_store.close_pools()
    games = []
    for fen, moves in [("8/4p3/8/3k4/7R/1q6/8/K7 w - - 0 1", ("h4d4", "d5d4")),
                       ("k7/8/1Q6/7r/3K4/8/4P3/8 b - - 0 1", ("h5d5", "d4d5"))]:
        game = chess.pgn.Game()
        game.setup(chess.Board(fen))
        game.headers.update({"Event": "Rated Blitz game", "Site": "https://lichess.org/synthet1", "WhiteElo": "1500",
                             "BlackElo": "1600", "Result": "1/2-1/2", "UTCDate": "2026.09.12", "TimeControl": "300+3"})
        game.add_variation(chess.Move.from_uci(moves[0])).add_variation(chess.Move.from_uci(moves[1]))
        games.append(str(game))
    candidates = list(mine_candidates(io.StringIO("\n\n".join(games)), "2026-09", MiningFilters(), new_counts(), strict=True))
    assert len(candidates) == 2
    bundle = build_bundle(candidates, "synthetic-postgres-stalemate-regression-v1")
    prove_bundle_import(bundle)
    print("test_postgres_stalemate_bundle_import_reimport_preserves_draft_content passed")


def prove_bundle_import(bundle):
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Stalemate import proof requires the disposable PostgreSQL runner")
    expected_exercise_count = len(bundle["tables"]["study_exercises"])
    study_id = bundle["tables"]["studies"][0]["id"]
    owned_study = False
    try:
        with postgres_store.connection() as database:
            assert not database.execute("SELECT 1 FROM studies WHERE id=?", (study_id,)).fetchone()
            assert not import_bundle(database, bundle)["idempotent"]
            owned_study = True
        postgres_store.close_pools()
        with postgres_store.connection() as database:
            reordered = deepcopy(bundle)
            for table_rows in reordered["tables"].values():
                table_rows.reverse()
            assert import_bundle(database, reordered)["idempotent"]
            exercises = list(database.execute("SELECT * FROM study_exercises WHERE study_id=?", (study_id,)))
            assert len(exercises) == expected_exercise_count and all(row["status"] == "draft" for row in exercises)
            assert database.execute("SELECT COUNT(*) FROM cards WHERE study_exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)", (study_id,)).fetchone()[0] == 0
            positions = list(database.execute("SELECT position.* FROM study_positions position JOIN study_sources source ON source.id=position.source_id JOIN study_chapters chapter ON chapter.id=source.chapter_id WHERE chapter.study_id=?", (study_id,)))
            assert len(positions) == 3 * expected_exercise_count
            terminal_nodes = [row for row in positions if row["node_path"] == "root.0.0"]
            assert len(terminal_nodes) == expected_exercise_count and all(chess.Board(row["fen"]).is_stalemate() for row in terminal_nodes)
            for exercise in exercises:
                specification = json.loads(database.execute("SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=?", (exercise["id"],)).fetchone()[0])
                assert specification["grading_policy"] == "open_judgment" and specification["mode"] == "single"
            changed = deepcopy(bundle)
            changed["tables"]["studies"][0]["title"] = "Must reject changed content"
            try:
                import_bundle(database, changed)
            except ValueError as error:
                assert "different content" in str(error)
            else:
                raise AssertionError("Changed stable Study content was silently accepted")
    finally:
        if owned_study:
            with postgres_store.connection() as database:
                database.execute("DELETE FROM study_exercise_revisions WHERE exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)", (study_id,))
                database.execute("DELETE FROM study_exercises WHERE study_id=?", (study_id,))
                for node_path in ("root.0.0", "root.0", "root"):
                    database.execute("DELETE FROM study_positions WHERE node_path=? AND source_id IN (SELECT source.id FROM study_sources source JOIN study_chapters chapter ON chapter.id=source.chapter_id WHERE chapter.study_id=?)", (node_path, study_id))
                database.execute("DELETE FROM study_sources WHERE chapter_id IN (SELECT id FROM study_chapters WHERE study_id=?)", (study_id,))
                database.execute("DELETE FROM study_chapters WHERE study_id=?", (study_id,))
                database.execute("DELETE FROM studies WHERE id=?", (study_id,))
        postgres_store.close_pools()


def test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content():
    corpus_directory = Path(__file__).resolve().parents[1] / "public/data/studies"
    for corpus_path in sorted(corpus_directory.glob("stalemate-swindles-*.tempo-study.json")):
        prove_bundle_import(json.loads(corpus_path.read_text()))
        print(f"test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content passed: {corpus_path.name}")


if __name__ == "__main__":
    test_postgres_stalemate_bundle_import_reimport_preserves_draft_content()
    test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content()
