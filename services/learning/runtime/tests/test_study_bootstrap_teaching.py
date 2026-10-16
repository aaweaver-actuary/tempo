"""Owning F2 bootstrap/teaching PostgreSQL regressions."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_runtime import activate
from test_runtime import bundle
import chess


def teaching(case, card, revision=1, ply=0, key=None):
    return case.client.post(f"/api/cards/{card}/teaching",
        json={"revision": revision, "ply": ply},
        headers={"Idempotency-Key": key or f"teaching:{card}:{revision}:{ply}"})


def test_study_bootstrap_reads_only_learning_owned_settings_without_issuing(case):
    response = case.client.get("/api/study/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1 and body["workspace_id"] == "workspace-a"
    assert body["timezone"] == "America/New_York"
    assert set(body["settings"]) == {"study_only_mode", "new_cards_per_day", "study_new_per_day",
        "tactics_new_per_day", "include_defensive_cards_in_daily_stack", "light_first_interval_days"}
    assert case.query("SELECT COUNT(*) FROM learning.queue_attempt_origins")[0][0] == 0


def test_study_settings_patch_is_durable_idempotent_and_has_no_optional_fields(case):
    args = dict(json={"study_only_mode": True, "tactics_new_per_day": 3},
                headers={"Idempotency-Key": "settings-one"})
    saved = case.client.post("/api/study/settings", **args)
    assert saved.status_code == 200, saved.text
    assert saved.json()["study_only_mode"] is True
    assert case.client.post("/api/study/settings", **args).json() == saved.json()
    assert case.client.get("/api/study/settings").json() == saved.json()
    assert case.client.post("/api/study/settings", json={"timezone": "UTC"},
        headers={"Idempotency-Key": "bad-timezone"}).status_code == 422
    assert case.client.post("/api/study/settings", json={"tactics_new_per_day": 4},
        headers={"Idempotency-Key": "settings-one"}).status_code == 409


def test_teaching_duplicate_fact_and_receipt_preserve_original_timestamp(case):
    card = activate(case)["facts"][0]["card_id"]
    first = teaching(case, card)
    assert first.status_code == 200, first.text
    assert teaching(case, card).json() == first.json()
    assert teaching(case, card, key="other-transport").json() == first.json()
    assert case.client.get(f"/api/cards/{card}/teaching").json() == {"states": [first.json()]}
    assert case.query("SELECT COUNT(*) FROM learning.teaching_states")[0][0] == 1
    for table in ["reviews", "review_attempt_receipts", "queue_attempt_origins"]:
        assert case.query(f"SELECT COUNT(*) FROM learning.{table}")[0][0] == 0


@pytest.mark.parametrize("revision,ply,status", [(2,0,409),(1,1,422),(1,-1,422),(0,0,422)])
def test_teaching_rejects_incompatible_revision_and_invalid_position(case, revision, ply, status):
    card = activate(case)["facts"][0]["card_id"]
    assert teaching(case, card, revision, ply).status_code == status
    assert case.query("SELECT COUNT(*) FROM learning.teaching_states")[0][0] == 0


def test_teaching_retirement_fences_new_writes_but_preserves_history_and_receipt(case):
    card = activate(case)["facts"][0]["card_id"]
    original = teaching(case, card).json()
    case.query("UPDATE learning.cards SET serving_active=0 WHERE id=%s", (card,))
    assert teaching(case, card).json() == original
    assert teaching(case, card, key="new-after-retire").status_code == 409
    assert case.client.get(f"/api/cards/{card}/teaching").json()["states"] == [original]


def test_teaching_concurrent_logical_delivery_is_incremental(case):
    card = activate(case)["facts"][0]["card_id"]
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda index: teaching(case, card, key=f"parallel-{index}"), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert case.query("SELECT COUNT(*) FROM learning.teaching_states")[0][0] == 1


def test_teaching_accepts_only_learner_positions_and_original_operation_payload(case):
    payload = bundle([("opening", {"type": "move_line", "accepted_lines": [["e2e4", "e7e5", "g1f3"]],
                                  "mode": "stepwise_line", "grading_policy": "reference"})])
    card = activate(case, payload)["facts"][0]["card_id"]
    assert teaching(case, card, ply=1).status_code == 422
    saved = teaching(case, card, ply=2)
    assert saved.status_code == 200, saved.text
    assert teaching(case, card, ply=0, key=f"teaching:{card}:1:2").status_code == 409


def test_teaching_deleted_or_unaccepted_definition_cannot_create_facts(case):
    card = activate(case)["facts"][0]["card_id"]
    case.query("INSERT INTO learning.deleted_cards(card_id) VALUES(%s)", (card,))
    assert teaching(case, card).status_code == 409
    assert case.query("SELECT COUNT(*) FROM learning.teaching_states")[0][0] == 0


def test_learning_move_membership_uses_accepted_routes_without_content_service(case):
    card = activate(case)["facts"][0]["card_id"]
    parameters = {"revision": 1, "fen": chess.STARTING_FEN, "move_uci": "e2e4"}
    response = case.client.get(f"/api/cards/{card}/move-membership", params=parameters)
    assert response.status_code == 200 and response.json()["verified"] is True
    response = case.client.get(f"/api/cards/{card}/move-membership", params={**parameters, "move_uci": "d2d4"})
    assert response.status_code == 200 and response.json()["verified"] is False
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
    case.query("UPDATE learning.cards SET serving_active=0 WHERE id=%s", (card,))
    assert case.client.get(f"/api/cards/{card}/move-membership", params=parameters).status_code == 409


def test_learning_endgame_list_retains_actual_frontend_envelope(case):
    from test_endgames import endgame_bundle
    activate(case, endgame_bundle())
    response = case.client.get("/api/endgames/templates")
    assert response.status_code == 200
    assert set(response.json()) == {"templates"}
    assert response.json()["templates"][0]["id"] == "template-a"


def test_learning_move_membership_preserves_all_accepted_executable_alternatives(case):
    payload = bundle([("opening", {"type": "move_line", "accepted_lines": [["e2e4"], ["d2d4"]],
                                  "mode": "single", "grading_policy": "reference"})])
    card = activate(case, payload)["facts"][0]["card_id"]
    response = case.client.get(f"/api/cards/{card}/move-membership",
        params={"revision": 1, "fen": chess.STARTING_FEN, "move_uci": "d2d4"})
    assert response.status_code == 200 and response.json()["verified"] is True


def test_learning_move_membership_rejects_malformed_chess_input_without_a_server_error(case):
    card = activate(case)["facts"][0]["card_id"]
    for fen, move in [("not-a-position", "e2e4"), (chess.STARTING_FEN, "not-a-move")]:
        assert case.client.get(f"/api/cards/{card}/move-membership",
            params={"revision": 1, "fen": fen, "move_uci": move}).status_code == 422


def test_learning_tactic_progress_is_persisted_not_a_default_settings_projection(case):
    assert case.client.get("/api/tactics/progress").json() == {}
    case.query("INSERT INTO learning.tactic_progress(puzzle_id,deck_id,clean_pass_at) VALUES('puzzle-a','pack-a','original-time')")
    assert case.client.get("/api/tactics/progress").json() == {
        "pack-a": {"index": 1, "clean": 1, "cleanIds": ["lichess-puzzle-a"], "discoveredIds": ["lichess-puzzle-a"]}}


@pytest.mark.parametrize("payload", [{}, {"study_only_mode": None}, {"tactics_new_per_day": -1}, {"light_first_interval_days": 0}, {"new_cards_per_day": True}])
def test_learning_settings_reject_invalid_fields_before_acceptance(case, payload):
    assert case.client.post("/api/study/settings", json=payload,
        headers={"Idempotency-Key": "invalid-settings"}).status_code == 422
    assert case.query("SELECT COUNT(*) FROM learning.operation_receipts")[0][0] == 0


@pytest.mark.parametrize("path", ["/api/study/bootstrap", "/api/study/settings", "/api/cards/missing/teaching"])
def test_study_startup_authentication_rejects_before_database_mutation(case, path):
    assert case.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert case.client.get(path, headers={"X-Tempo-Workspace": "other"}).status_code == 403
    assert case.client.get(path, headers={"Origin": "https://disallowed.invalid"}).status_code == 403
    assert case.query("SELECT COUNT(*) FROM learning.operation_receipts")[0][0] == 0
