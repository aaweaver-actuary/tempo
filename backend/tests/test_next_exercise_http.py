"""HTTP compatibility through the production composition and Study operation."""

import copy
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import main
from app.product_composition import study
from app.study.next_exercise import QueueReadSnapshot

CARD = {
    "queue_entry_id": 1,
    "id": "card",
    "revision": 3,
    "content_type": "study_exercise",
    "study_exercise_id": "ex",
    "start_fen": "fen",
    "effective_trained_color": "white",
}


@pytest.fixture
def setup(monkeypatch):
    state = {
        "cards": [CARD],
        "issue": (None, {"issued": True}, None),
        "revision": {"current_revision": 4, "specification_json": '{"moves": []}'},
        "reads": [],
        "limits": [],
    }

    @contextmanager
    def connection():
        yield object()

    monkeypatch.setattr(main, "read_connection", connection)
    monkeypatch.setattr(
        study,
        "read_queue_snapshot",
        lambda limit, **kw: (
            state["limits"].append(limit)
            or QueueReadSnapshot(
                "day",
                "UTC",
                {"state": "ready"},
                [],
                copy.deepcopy(state["cards"]),
                len(state["cards"]),
            )
        ),
    )
    monkeypatch.setattr(study, "_issue_facts", lambda *a, **kw: state["issue"])
    monkeypatch.setattr(
        main.queue_projection_storage, "queue_rows", lambda *a, **kw: [CARD]
    )
    monkeypatch.setattr(
        main.queue_projection_storage,
        "exercise_revision",
        lambda db, key: state["reads"].append(key) or state["revision"],
    )
    from app import queue_commands

    monkeypatch.setattr(queue_commands, "current_study_day", lambda db: "day")
    from app.study import position_checkpoint

    monkeypatch.setattr(
        position_checkpoint,
        "queue_manifests",
        lambda cards: [
            card.update(opening_decision_manifest={"scope": "ok"}) for card in cards
        ],
    )
    return state, TestClient(main.app)


@pytest.mark.parametrize(
    "path,limit,prepared",
    [("today", None, False), ("prepared", None, True), ("window", 20, False)],
)
@pytest.mark.parametrize(
    "query,evidence", [("", False), ("?include_opening_evidence=true", True)]
)
def test_real_routes_preserve_queue_and_negotiation(
    setup, path, limit, prepared, query, evidence
):
    state, client = setup
    response = client.get(f"/api/queue/{path}{query}")
    assert response.status_code == 200
    body = response.json()
    assert (
        body["local_date"] == "day"
        and body["count"] == 1
        and body["projection"] == {"state": "ready"}
    )
    card = body["cards"][0]
    assert ("opening_decision_manifest" in card) is evidence
    assert state["limits"] == [limit]
    assert ("prepared_at" in body) is prepared
    if prepared:
        assert (
            datetime.fromisoformat(body["prepared_at"]).utcoffset().total_seconds() == 0
        )
        assert card["study_snapshot"] == {
            "schema_version": 1,
            "grader_version": 1,
            "exercise_id": "ex",
            "revision": 4,
            "fen": "fen",
            "specification": {"moves": []},
        }
        assert state["reads"] == ["ex"]
    else:
        assert state["reads"] == []


@pytest.mark.parametrize(
    "limit,code", [(0, 422), (1, 200), (20, 200), (21, 422), ("bad", 422)]
)
def test_window_bounds(setup, limit, code):
    assert setup[1].get(f"/api/queue/window?limit={limit}").status_code == code


@pytest.mark.parametrize("path", ["today", "prepared", "window"])
def test_pending_keeps_identity_and_skips_preparation(setup, path):
    state, client = setup
    pending = JSONResponse(
        {"operation_id": "original", "state": "queued", "message": "wait"},
        status_code=202,
        headers={"Location": "/api/operations/original", "Retry-After": "1"},
    )
    state["issue"] = ({"kind": "pending", "operation_id": "original"}, None, pending)
    response = client.get(f"/api/queue/{path}")
    assert response.status_code == 202 and response.json() == {
        "operation_id": "original",
        "state": "queued",
        "message": "wait",
    }
    assert (
        response.headers["location"] == "/api/operations/original"
        and response.headers["retry-after"] == "1"
    )
    assert state["reads"] == []


@pytest.mark.parametrize("path", ["today", "prepared", "window"])
def test_conflict_and_flat_hold_and_empty(setup, path):
    state, client = setup
    state["issue"] = (
        {"kind": "held", "reason": "position_attempt_identity_conflict"},
        None,
        None,
    )
    assert client.get(f"/api/queue/{path}").status_code == 409
    state["issue"] = (
        None,
        {
            "issued": False,
            "position_study_mode": "position",
            "position_presentation": None,
            "position_study_hold": "position_not_issued",
        },
        None,
    )
    response = client.get(f"/api/queue/{path}")
    assert (
        response.status_code == 200
        and response.json()["cards"][0]["position_study_hold"] == "position_not_issued"
    )
    state["cards"] = []
    response = client.get(f"/api/queue/{path}")
    assert response.status_code == 200 and response.json()["cards"] == []


def test_prepared_missing_revision_is_original_conflict(setup):
    state, client = setup
    state["revision"] = None
    response = client.get("/api/queue/prepared")
    assert response.status_code == 409 and response.json() == {
        "detail": "Study exercise ex has no current revision"
    }


@pytest.mark.parametrize("path", ["today", "prepared", "window"])
def test_unavailable_and_invalid_boolean(setup, path):
    state, client = setup
    state["issue"] = (
        {"kind": "unavailable", "reason": "head_changed", "retryable": True},
        None,
        None,
    )
    response = client.get(f"/api/queue/{path}")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "The queue head changed. Retry loading the current queue."
    }
    assert state["reads"] == []
    assert (
        client.get(f"/api/queue/{path}?include_opening_evidence=invalid").status_code
        == 422
    )


def test_prepared_reads_exercise_revisions_in_queue_order(setup):
    state, client = setup
    state["cards"] = [
        CARD,
        {**CARD, "id": "second", "queue_entry_id": 2, "study_exercise_id": "other"},
    ]
    response = client.get("/api/queue/prepared")
    assert response.status_code == 200 and state["reads"] == ["ex", "other"]
