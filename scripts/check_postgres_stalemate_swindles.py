"""Generated portable-content import proof, only in the disposable durability stack."""

from copy import deepcopy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import uuid

import chess
import chess.pgn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store
from app.services.stalemate_swindles import MiningFilters, build_bundle, mine_candidates, new_counts
from app.services.study_portable import import_bundle


def test_postgres_stalemate_bundle_import_reimport_preserves_draft_content():
    prove_bundle_import(synthetic_bundle("synthetic-postgres-stalemate-regression-v1"))
    print("test_postgres_stalemate_bundle_import_reimport_preserves_draft_content passed")


def synthetic_bundle(corpus_id):
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
    return build_bundle(candidates, corpus_id)


@contextmanager
def disposable_postgres_configuration():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Stalemate import proof requires the disposable PostgreSQL runner")
    database_variables = ("TEMPO_DATABASE_WRITE_URL", "TEMPO_DATABASE_READ_URL")
    previous_configuration = {variable: os.environ.get(variable) for variable in database_variables}
    try:
        for variable in database_variables:
            os.environ[variable] = "postgresql://postgres@postgres:5432/tempo"
        postgres_store.close_pools()
        yield
    finally:
        try:
            postgres_store.close_pools()
        finally:
            for variable, previous_value in previous_configuration.items():
                if previous_value is None:
                    os.environ.pop(variable, None)
                else:
                    os.environ[variable] = previous_value


def review_history_snapshot(database):
    histories = {
        table: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                      for row in database.execute(f"SELECT * FROM {table}"))
        for table in ("reviews", "study_attempts", "review_attempt_receipts", "review_schedule_snapshots")
    }
    # Background publication may update unrelated cards; scheduling belongs to
    # the foreground and must preserve every already-reviewed card exactly.
    histories["reviewed_cards"] = sorted(json.dumps(dict(row), sort_keys=True, default=str)
        for row in database.execute("SELECT * FROM cards WHERE id IN (SELECT card_id FROM reviews)"))
    return histories


def prove_bundle_import(bundle, preserved_review_history=None):
    with disposable_postgres_configuration():
        expected_exercise_count = len(bundle["tables"]["study_exercises"])
        study_id = bundle["tables"]["studies"][0]["id"]
        owned_study = False
        try:
            with postgres_store.connection() as database:
                assert not database.execute("SELECT 1 FROM studies WHERE id=?", (study_id,)).fetchone()
                assert not import_bundle(database, bundle)["idempotent"]
                owned_study = True
                if preserved_review_history is not None:
                    assert review_history_snapshot(database) == preserved_review_history, "Import changed review history"
            postgres_store.close_pools()
            with postgres_store.connection() as database:
                reordered = deepcopy(bundle)
                for table_rows in reordered["tables"].values():
                    table_rows.reverse()
                assert import_bundle(database, reordered)["idempotent"]
                if preserved_review_history is not None:
                    assert review_history_snapshot(database) == preserved_review_history, "Reimport changed review history"
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
                if preserved_review_history is not None:
                    assert review_history_snapshot(database) == preserved_review_history, "Rejected import changed review history"
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


def test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content():
    corpus_directory = Path(__file__).resolve().parents[1] / "public/data/studies"
    for corpus_path in sorted(corpus_directory.glob("stalemate-swindles-*.tempo-study.json")):
        prove_bundle_import(json.loads(corpus_path.read_text()))
        print(f"test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content passed: {corpus_path.name}")


def test_postgres_stalemate_import_reimport_preserves_existing_review_history():
    from app import study_commands, study_attempt_commands  # Register the real foreground handlers.
    from app.command_gateway import execute_command

    with disposable_postgres_configuration():
        reviewed_fixture = synthetic_bundle("synthetic-postgres-stalemate-reviewed-history-v1")
        fixture_study_id = reviewed_fixture["tables"]["studies"][0]["id"]
        fixture_exercise_id = reviewed_fixture["tables"]["study_exercises"][0]["id"]
        with postgres_store.connection() as database:
            assert not import_bundle(database, reviewed_fixture)["idempotent"]
        payload = {"study_id": fixture_study_id, "exercise_id": fixture_exercise_id}
        enrolled = execute_command(str(uuid.uuid4()), "studies.exercises.enroll", payload)
        queued = execute_command(str(uuid.uuid4()), "studies.exercises.train_now", payload)
        with postgres_store.connection() as database:
            cycle = database.execute("SELECT cycle FROM daily_queue WHERE id=?", (queued["queue_entry_id"],)).fetchone()[0]
        reference = json.loads(reviewed_fixture["tables"]["study_exercise_revisions"][0]["specification_json"])
        result = execute_command(str(uuid.uuid4()), "studies.attempts.submit", {**payload, "attempt": {
            "attempt_id": str(uuid.uuid4()), "revision": 1, "context": "review",
            "card_id": enrolled["card_id"], "queue_entry_id": queued["queue_entry_id"], "queue_cycle": cycle,
            "answer": {"type": "move_line", "moves": reference["accepted_lines"][0]},
        }})
        assert result["persisted"] and result["rating"] == "correct" and result["review"]["review_id"]
        with postgres_store.connection() as database:
            preserved = review_history_snapshot(database)
            assert all(preserved.values()), "History protection must use nonempty real reviewed fixtures"
        corpus_directory = Path(__file__).resolve().parents[1] / "public/data/studies"
        for corpus_path in sorted(corpus_directory.glob("stalemate-swindles-*.tempo-study.json")):
            prove_bundle_import(json.loads(corpus_path.read_text()), preserved)
        # The reviewed fixture belongs to this disposable stack and remains for
        # runner teardown, preserving its real command receipts and history.
        print("test_postgres_stalemate_import_reimport_preserves_existing_review_history passed")


if __name__ == "__main__":
    # A fresh schema process has no read/write URLs: the real corpus must work first.
    test_postgres_checked_in_stalemate_corpora_import_reimport_preserves_draft_content()
    test_postgres_stalemate_bundle_import_reimport_preserves_draft_content()
    test_postgres_stalemate_import_reimport_preserves_existing_review_history()
