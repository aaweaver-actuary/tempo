"""Named regressions for authored studies and the shared review queue."""

import json
import sqlite3
import time
import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import database
from app.main import app, enqueue_daily_queue_refresh
from app.study_contracts import ExerciseAnswer, ExerciseSpecification
from app.study_migration import migrate_studies
from app.services.study_grading import evaluate_answer, validate_exercise
from app.services.study_pgn import preview_pgn


FEN = "4k3/8/8/8/8/8/8/4K1N1 w - - 0 1"
ROOT_PGN = f'[Event "Original synthetic position"]\n[SetUp "1"]\n[FEN "{FEN}"]\n\n*\n'


def make_study(client):
    study_id = client.post("/api/studies", json={"title": "Original examples"}).json()["id"]
    chapter_id = client.post(f"/api/studies/{study_id}/chapters", json={"title": "Knight geometry"}).json()["id"]
    preview = client.post(f"/api/studies/{study_id}/import/preview", json={"chapter_id": chapter_id, "raw_pgn": ROOT_PGN})
    assert preview.status_code == 200
    commit = client.post(f"/api/studies/{study_id}/import/commit", json={"chapter_id": chapter_id,
        "raw_pgn": ROOT_PGN, "preview_digest": preview.json()["digest"], "selected_records": [0]})
    assert commit.status_code == 200, commit.text
    position_id = client.get(f"/api/studies/{study_id}/chapters/{chapter_id}").json()["positions"][0]["id"]
    return study_id, chapter_id, position_id


def wait_for_study_card(client, exercise_id):
    for _ in range(100):
        response = client.get("/api/queue/today")
        if response.status_code == 200:
            for card in response.json()["cards"]:
                if card.get("study_exercise_id") == exercise_id:
                    return card
        time.sleep(0.02)
    raise AssertionError("Study card did not enter the daily queue")


def test_study_fen_only_import_preserves_root_and_does_not_enroll(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        before = client.get("/api/queue/today").json()["count"]
        study_id, chapter_id, position_id = make_study(client)
        chapter = client.get(f"/api/studies/{study_id}/chapters/{chapter_id}").json()
        assert len(chapter["positions"]) == 1
        assert chapter["positions"][0]["id"] == position_id
        assert chapter["positions"][0]["move_uci"] is None
        assert json.loads(chapter["positions"][0]["history_json"]) == []
        assert client.get("/api/queue/today").json()["count"] == before
        preview = client.post(f"/api/studies/{study_id}/import/preview", json={"chapter_id": chapter_id, "raw_pgn": ROOT_PGN}).json()
        duplicate = client.post(f"/api/studies/{study_id}/import/commit", json={"chapter_id": chapter_id,
            "raw_pgn": ROOT_PGN, "preview_digest": preview["digest"], "selected_records": [0]}).json()
        assert duplicate["idempotent"]
        assert len(client.get(f"/api/studies/{study_id}/chapters/{chapter_id}").json()["positions"]) == 1


def test_study_changed_source_update_preserves_old_occurrences_and_rubrics(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, chapter_id, old_position_id = make_study(client)
        chapter = client.get(f"/api/studies/{study_id}/chapters/{chapter_id}").json()
        source_group_id = chapter["sources"][0]["source_group_id"]
        exercise_id = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": old_position_id,
            "specification": {"type": "square_set", "prompt": "Mark the knight", "criterion": "White knight",
                              "required": ["g1"]}}).json()["id"]
        changed_pgn = ROOT_PGN.replace("Original synthetic position", "Revised synthetic position")
        preview = client.post(f"/api/studies/{study_id}/import/preview", json={
            "chapter_id": chapter_id, "source_group_id": source_group_id, "raw_pgn": changed_pgn}).json()
        payload = {"chapter_id": chapter_id, "source_group_id": source_group_id,
                   "raw_pgn": changed_pgn, "preview_digest": preview["digest"],
                   "selected_records": [0], "mode": "update"}
        update = client.post(f"/api/studies/{study_id}/import/commit", json=payload)
        assert update.status_code == 200, update.text
        assert update.json()["version"] == 2
        assert client.post(f"/api/studies/{study_id}/import/commit", json=payload).json()["idempotent"]
        refreshed = client.get(f"/api/studies/{study_id}/chapters/{chapter_id}").json()
        assert [source["version"] for source in refreshed["sources"]] == [1, 2]
        assert len(refreshed["positions"]) == 2
        assert old_position_id in {position["id"] for position in refreshed["positions"]}
        assert refreshed["exercises"][0]["id"] == exercise_id
        assert refreshed["exercises"][0]["position_id"] == old_position_id
        assert refreshed["exercises"][0]["specification"]["required"] == ["g1"]


def test_study_square_exercise_from_fen_only_position_reviews_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        created = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": position_id,
            "specification": {"type": "square_set", "prompt": "Select the knight square", "criterion": "The square occupied by the white knight",
                              "required": ["g1"]}})
        assert created.status_code == 200, created.text
        exercise_id = created.json()["id"]
        assert client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll").status_code == 200
        card = wait_for_study_card(client, exercise_id)
        assert card["repertoire_id"] is None and card["moves"] == []
        request = {"attempt_id": str(uuid.uuid4()), "revision": 1, "answer": {"type": "square_set", "squares": ["g1"]},
                   "context": "review", "card_id": card["id"], "queue_entry_id": card["queue_entry_id"], "queue_cycle": card["cycle"]}
        endpoint = f"/api/studies/{study_id}/exercises/{exercise_id}/attempts"
        first = client.post(endpoint, json=request)
        assert first.status_code == 200, first.text
        assert first.json()["rating"] == "correct"
        retry = client.post(endpoint, json=request)
        assert retry.status_code == 200 and retry.json() == first.json()
        changed = client.post(endpoint, json={**request, "answer": {"type": "square_set", "squares": []}})
        assert changed.status_code == 409
        another = client.post(endpoint, json={**request, "attempt_id": str(uuid.uuid4())})
        assert another.status_code == 409
        with database.read_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],)).fetchone()[0] == 1
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_study_correct_answer_after_hint_is_saved_as_guided_again(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        exercise_id = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": position_id,
            "specification": {"type": "square_set", "prompt": "Mark the knight", "criterion": "White knight",
                              "required": ["g1"], "hint": "Look on the back rank"}}).json()["id"]
        client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll")
        card = wait_for_study_card(client, exercise_id)
        attempt = client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/attempts", json={
            "attempt_id": str(uuid.uuid4()), "revision": 1,
            "answer": {"type": "square_set", "squares": ["g1"]}, "context": "review",
            "card_id": card["id"], "queue_entry_id": card["queue_entry_id"], "queue_cycle": card["cycle"],
            "hint_seen": True,
        })
        assert attempt.status_code == 200, attempt.text
        assert attempt.json()["assessment"]["outcome"] == "correct"
        assert attempt.json()["rating"] == "again"
        with database.read_connection() as connection:
            review = connection.execute("SELECT rating,guided FROM reviews WHERE card_id=?", (card["id"],)).fetchone()
            assert tuple(review) == ("again", 1)


def test_study_attempt_rejects_card_revision_mismatch_without_scheduling(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        exercise_id = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": position_id,
            "specification": {"type": "square_set", "prompt": "Mark the knight", "criterion": "White knight",
                              "required": ["g1"]}}).json()["id"]
        client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll")
        card = wait_for_study_card(client, exercise_id)
        with database.connection() as connection:
            connection.execute("UPDATE cards SET revision=2 WHERE id=?", (card["id"],))
        attempt = client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/attempts", json={
            "attempt_id": str(uuid.uuid4()), "revision": 1,
            "answer": {"type": "square_set", "squares": ["g1"]}, "context": "review",
            "card_id": card["id"], "queue_entry_id": card["queue_entry_id"], "queue_cycle": card["cycle"],
        })
        assert attempt.status_code == 409
        with database.read_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM study_attempts").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],)).fetchone()[0] == 0


def test_study_assessment_revision_reset_preserves_old_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        specification = {"type": "choice", "prompt": "Is there a threat?", "options": [
            {"id": "yes", "text": "Yes"}, {"id": "no", "text": "No"}], "correct_option_ids": ["no"]}
        exercise_id = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": position_id,
            "specification": specification}).json()["id"]
        old_card = client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll").json()["card_id"]
        changed_specification = {**specification, "correct_option_ids": ["yes"]}
        revision = client.put(f"/api/studies/{study_id}/exercises/{exercise_id}", json={
            "expected_revision": 1, "specification": changed_specification})
        assert revision.status_code == 200, revision.text
        assert revision.json()["schedule_reset"]
        with database.read_connection() as connection:
            cards = connection.execute("SELECT id,archived,revision FROM cards WHERE study_exercise_id=? ORDER BY archived", (exercise_id,)).fetchall()
            assert len(cards) == 2 and cards[0]["id"] != old_card
            assert cards[0]["revision"] == 2 and cards[1]["archived"] == 1
        stale = client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/attempts", json={
            "attempt_id": str(uuid.uuid4()), "revision": 1, "answer": {"type": "choice", "option_ids": ["no"]},
            "context": "practice"})
        assert stale.status_code == 409


def test_study_native_bundle_preserves_identity_and_rejects_changed_content(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        exercise_id = client.post(f"/api/studies/{study_id}/exercises", json={"position_id": position_id,
            "specification": {"type": "choice", "prompt": "Where is the knight?", "options": [
                {"id": "g1", "text": "g1"}, {"id": "h1", "text": "h1"}], "correct_option_ids": ["g1"]}}).json()["id"]
        assert client.post(f"/api/studies/{study_id}/links", json={"source_position_id": position_id,
            "target_exercise_id": exercise_id, "relation": "illustrates"}).status_code == 200
        bundle = client.get(f"/api/studies/{study_id}/export").json()
        assert "study_attempts" not in bundle["tables"] and "cards" not in bundle["tables"]
        assert client.post("/api/studies/import-bundle", json={"bundle": bundle}).json()["idempotent"]
        copied = client.post("/api/studies/import-bundle", json={"bundle": bundle, "mode": "copy"})
        assert copied.status_code == 200, copied.text
        assert copied.json()["study_id"] != study_id
        copied_bundle = client.get(f"/api/studies/{copied.json()['study_id']}/export").json()
        copied_position = copied_bundle["tables"]["study_positions"][0]
        copied_exercise = copied_bundle["tables"]["study_exercises"][0]
        assert copied_position["id"] != position_id
        assert copied_exercise["position_id"] == copied_position["id"]
        assert copied_bundle["tables"]["study_links"][0]["target_exercise_id"] == copied_exercise["id"]
        changed = json.loads(json.dumps(bundle))
        changed["tables"]["studies"][0]["title"] = "Conflicting title"
        assert client.post("/api/studies/import-bundle", json={"bundle": changed}).status_code == 422
        assert client.post("/api/studies/import-bundle", json={"bundle": {**bundle, "schema_version": 2}}).status_code == 422
        extra_column = json.loads(json.dumps(bundle))
        extra_column["tables"]["studies"][0]["unexpected_column"] = "reject"
        assert client.post("/api/studies/import-bundle", json={"bundle": extra_column, "mode": "copy"}).status_code == 422


def test_study_migration_preserves_legacy_cards_reviews_and_foreign_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    from app import study_migration

    with monkeypatch.context() as legacy_schema:
        legacy_schema.setattr(study_migration, "migrate_studies", lambda: None)
        database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('legacy','Legacy','legacy.pgn','2026-01-01')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('legacy-card','legacy','prefix',?,'[\"g1f3\"]','learning','2026-01-01')", (FEN,))
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('legacy-card','correct','2026-01-01',0,1)")
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-01-01','legacy-card',0)")
    migrate_studies()
    migrate_studies()
    with database.read_connection() as connection:
        assert connection.execute("SELECT repertoire_id FROM cards WHERE id='legacy-card'").fetchone()[0] == "legacy"
        assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id='legacy-card'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='legacy-card'").fetchone()[0] == 1
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert (tmp_path / "tempo.db.before-studies-v1.bak").exists()
    with sqlite3.connect(tmp_path / "tempo.db.before-studies-v1.bak") as backup:
        assert backup.execute("SELECT COUNT(*) FROM reviews WHERE card_id='legacy-card'").fetchone()[0] == 1


def test_study_python_grader_matches_original_golden_cases():
    cases = json.loads((Path(__file__).parents[2] / "tests/fixtures/study-grading.json").read_text())
    specification_adapter = TypeAdapter(ExerciseSpecification)
    answer_adapter = TypeAdapter(ExerciseAnswer)
    for case in cases:
        specification = specification_adapter.validate_python(case["specification"])
        answer = answer_adapter.validate_python(case["answer"])
        validate_exercise(specification, case["fen"])
        assert evaluate_answer(specification, answer, case["fen"])["outcome"] == case["outcome"], case["name"]


def test_study_answer_revealing_sibling_is_buried_until_explicit_practice(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        exercise_ids = []
        for index in range(2):
            response = client.post(f"/api/studies/{study_id}/exercises", json={
                "position_id": position_id,
                "specification": {"type": "square_set", "prompt": f"Mark the knight square {index}",
                                  "criterion": "White knight", "required": ["g1"]},
            })
            assert response.status_code == 200, response.text
            exercise_id = response.json()["id"]
            exercise_ids.append(exercise_id)
            assert client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll").status_code == 200
        first_card = wait_for_study_card(client, exercise_ids[0])
        wait_for_study_card(client, exercise_ids[1])
        answer = {"attempt_id": str(uuid.uuid4()), "revision": 1,
                  "answer": {"type": "square_set", "squares": ["g1"]}, "context": "review",
                  "card_id": first_card["id"], "queue_entry_id": first_card["queue_entry_id"],
                  "queue_cycle": first_card["cycle"]}
        completed = client.post(f"/api/studies/{study_id}/exercises/{exercise_ids[0]}/attempts", json=answer)
        assert completed.status_code == 200, completed.text
        queue = client.get("/api/queue/today").json()
        assert all(card.get("study_exercise_id") != exercise_ids[1] for card in queue["cards"])
        override = client.post(f"/api/studies/{study_id}/exercises/{exercise_ids[1]}/train-now")
        assert override.status_code == 200, override.text
        assert wait_for_study_card(client, exercise_ids[1])["queue_entry_id"] == override.json()["queue_entry_id"]


def test_study_pgn_preview_keeps_variations_annotations_and_invalid_record_diagnostics():
    pgn = ('[Event "Original branches"]\n[SetUp "1"]\n[FEN "' + FEN + '"]\n\n'
           '1. Nf3 {[%cal Gg1f3] first branch} (1. Nh3 $1 {second branch}) *\n')
    preview = preview_pgn(pgn)
    record = preview["records"][0]
    assert record["valid"] and len(record["nodes"]) == 3
    assert record["nodes"][0]["history"] == []
    assert [node["move_uci"] for node in record["nodes"][1:]] == ["g1f3", "g1h3"]
    assert record["nodes"][1]["arrows"] == [{"from": "g1", "to": "f3", "color": "green"}]
    assert record["nodes"][2]["nags"] == [1]
    invalid = preview_pgn('[Event "Invalid"]\n\n1. e4 e5 2. Qh5?? Nc6 3. Qh5 *\n')["records"][0]
    assert not invalid["valid"] and invalid["diagnostics"]


def test_study_migration_failure_rolls_back_and_backup_restores(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    from app import study_migration

    with monkeypatch.context() as legacy_schema:
        legacy_schema.setattr(study_migration, "migrate_studies", lambda: None)
        database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('legacy','Legacy','legacy.pgn','2026-01-01')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('legacy-card','legacy','prefix',?,'[]','new','2026-01-01')", (FEN,))
    with monkeypatch.context() as failed_migration:
        failed_migration.setattr(study_migration, "STUDY_TABLES", (*study_migration.STUDY_TABLES, "THIS IS INVALID SQL"))
        try:
            migrate_studies()
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Broken migration unexpectedly succeeded")
    with database.read_connection() as connection:
        assert connection.execute("SELECT id FROM cards WHERE id='legacy-card'").fetchone()[0] == "legacy-card"
        assert not connection.execute("SELECT 1 FROM internal_migrations WHERE name='studies-v1'").fetchone()
    backup_path = tmp_path / "tempo.db.before-studies-v1.bak"
    assert backup_path.exists()
    with sqlite3.connect(backup_path) as backup, sqlite3.connect(database.DB_PATH) as restored:
        backup.backup(restored)
    migrate_studies()
    with database.read_connection() as connection:
        assert connection.execute("SELECT id FROM cards WHERE id='legacy-card'").fetchone()[0] == "legacy-card"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_study_new_exercise_allowance_is_independent_and_due_reviews_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        study_id, _, position_id = make_study(client)
        with database.connection() as connection:
            connection.execute("UPDATE settings SET study_new_per_day=2,new_cards_per_day=0 WHERE id=1")
        exercise_ids = []
        for index in range(3):
            response = client.post(f"/api/studies/{study_id}/exercises", json={
                "position_id": position_id,
                "specification": {"type": "square_set", "prompt": f"Knight location {index}",
                                  "criterion": "White knight", "required": ["g1"]},
            })
            exercise_id = response.json()["id"]
            exercise_ids.append(exercise_id)
            assert client.post(f"/api/studies/{study_id}/exercises/{exercise_id}/enroll").status_code == 200
        for _ in range(100):
            cards = client.get("/api/queue/today").json()["cards"]
            queued_ids = {card.get("study_exercise_id") for card in cards}
            if len(queued_ids.intersection(exercise_ids)) == 2:
                break
            time.sleep(0.02)
        assert len(queued_ids.intersection(exercise_ids)) == 2
        assert exercise_ids[2] not in queued_ids
        with database.connection() as connection:
            card = connection.execute("SELECT id FROM cards WHERE study_exercise_id=?", (exercise_ids[2],)).fetchone()
            connection.execute("UPDATE cards SET state='learning',due_date=? WHERE id=?", (date.today().isoformat(), card[0]))
            connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,?,?,0,1)",
                               (card[0], "correct", "2026-01-01"))
        enqueue_daily_queue_refresh()
        for _ in range(100):
            queued_ids = {card.get("study_exercise_id") for card in client.get("/api/queue/today").json()["cards"]}
            if exercise_ids[2] in queued_ids:
                break
            time.sleep(0.02)
        assert exercise_ids[2] in queued_ids
