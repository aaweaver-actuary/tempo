"""B27: retired Coverage input never becomes ordinary authored content."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app import main
from app.analysis_paste_commands import commit_analysis_paste
from app.branch_commands import add_repertoire_branch
from app.contracts.transport.authored_lines import AnalysisPasteCommitRequest, AnalysisPastePreviewRequest


@pytest.mark.parametrize("gap", ["node:e2e4", "bad", "", 17, {"old": "bytes"}])
def test_retired_gap_is_rejected_before_preview_or_command_storage(monkeypatch, gap):
    def unexpected(*args, **kwargs):
        pytest.fail("retired input reached source or storage")
    monkeypatch.setattr(main, "read_connection", unexpected)
    with pytest.raises(HTTPException) as preview:
        main.preview_analysis_paste(AnalysisPastePreviewRequest(text="x", source_gap_id=gap))
    with pytest.raises(HTTPException) as paste:
        commit_analysis_paste(object(), {"request": {"source_gap_id": gap}})
    with pytest.raises(HTTPException) as branch:
        add_repertoire_branch(object(), {"source_gap_id": gap})
    for failure in (preview, paste, branch):
        assert (failure.value.status_code, failure.value.detail) == (410, "Coverage authoring is retired")


def test_completed_original_gap_receipt_replays_before_retirement(monkeypatch):
    from app import command_gateway
    original = {"text": "x", "source_gap_id": {"old": "bytes"}, "preview_token": "old", "selections": []}
    request = AnalysisPasteCommitRequest.model_validate(original)
    expected_hash = command_gateway.request_digest("analysis.paste.commit", {"request": request.model_dump(mode="json")})
    calls = []
    def read(key, *, command_name, request_hash):
        calls.append((key, command_name, request_hash))
        return {"state": "complete", "response": {"original": "receipt"}}
    monkeypatch.setattr(command_gateway, "read_operation", read)
    monkeypatch.setattr(main, "read_connection", lambda: pytest.fail("source read on replay"))
    assert main.commit_analysis_paste(request, "original-id") == {"original": "receipt"}
    assert calls == [("original-id", "analysis.paste.commit", expected_hash)]
    assert request.model_dump(mode="json")["source_gap_id"] == original["source_gap_id"]


def test_unfinished_original_gap_receipt_remains_unchanged(monkeypatch):
    from app import command_gateway
    request = AnalysisPasteCommitRequest(text="x", source_gap_id="node:e2e4", preview_token="old", selections=[])
    calls = []
    monkeypatch.setattr(command_gateway, "read_operation", lambda key, **kwargs: calls.append((key, kwargs)) or {"state": "pending"})
    monkeypatch.setattr(main, "read_connection", lambda: pytest.fail("source read on rejection"))
    with pytest.raises(HTTPException) as failure:
        main.commit_analysis_paste(request, "original-id")
    assert failure.value.status_code == 410
    assert calls == [("original-id", {"command_name": "analysis.paste.commit", "request_hash": command_gateway.request_digest("analysis.paste.commit", {"request": request.model_dump(mode="json")})})]
    assert request.source_gap_id == "node:e2e4"


@pytest.mark.parametrize("payload", [{"text": "x"}, {"text": "x", "source_gap_id": None}])
def test_gap_free_preview_follows_normal_path(monkeypatch, payload):
    @contextmanager
    def connection():
        yield "database"
    monkeypatch.setattr(main, "read_connection", connection)
    monkeypatch.setattr(main, "build_paste_preview", lambda *args: args)
    assert main.preview_analysis_paste(AnalysisPastePreviewRequest.model_validate(payload)) == ("database", "x", None, None)


# Real PostgreSQL is started only when this explicit native test is selected.
from native_study_fixture import owned_postgres


@pytest.mark.parametrize("command,payload", [
    ("repertoire.branch.add", {"repertoire_id": "white", "starting_fen": "original", "moves": [],
        "trained_color": "white", "source_gap_id": "node:e2e4"}),
    ("analysis.paste.commit", {"request": {"text": "original", "source_gap_id": {"old": "bytes"},
        "preview_token": "original", "selections": []}, "prepared_preview": {"original": "bytes"}}),
])
def test_native_recovery_keeps_original_receipt_and_rejects_retired_gap(owned_postgres, monkeypatch, command, payload):
    import json
    import uuid
    from contextlib import contextmanager
    from app import command_gateway

    @contextmanager
    def writer(background):
        assert not background
        with owned_postgres() as database:
            yield database
    monkeypatch.setattr(command_gateway, "_writer_connection", writer)
    operation_id = "b27-legacy-" + uuid.uuid4().hex
    complete_id = operation_id + "-complete"
    digest = command_gateway.request_digest(command, payload)
    # Preserve the exact original stored JSON, including formatting, across recovery.
    original_bytes = json.dumps(payload, indent=2, sort_keys=True)
    response_bytes = '{"accepted":"original","count":1}'
    tables = ("repertoires", "repertoire_lines", "repertoire_line_training_depths",
              "repertoire_coverage_runs", "repertoire_coverage_nodes", "repertoire_coverage_candidates")
    def snapshot(database):
        return {table: database.execute_native(f"SELECT COALESCE(json_agg(t ORDER BY t::text)::text, '[]') FROM {table} t").fetchone()[0]
                for table in tables}
    with owned_postgres() as db:
        before = snapshot(db)
        db.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background) VALUES(%s,%s,%s,'queued',%s,false)",
                          (operation_id, command, digest, original_bytes))
        db.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,response_json,background) VALUES(%s,%s,%s,'complete',%s,%s,false)",
                          (complete_id, command, digest, original_bytes, response_bytes))
    should_run, retained, token, _ = command_gateway.record_operation_attempt(operation_id, command, payload, background=False)
    assert should_run and token and retained == payload
    assert command_gateway.execute_command(operation_id, command, retained, attempt_token=token) is None
    with owned_postgres() as db:
        row = db.execute_native("SELECT operation_id,command_name,request_hash,state,payload_json,error_json FROM operation_receipts WHERE operation_id=%s", (operation_id,)).fetchone()
        assert tuple(row)[:5] == (operation_id, command, digest, "failed", original_bytes)
        error = json.loads(row[5])
        assert error["status_code"] == 410 and error["detail"] == "Coverage authoring is retired"
        assert snapshot(db) == before
    assert command_gateway.execute_command(complete_id, command, payload) == json.loads(response_bytes)
    assert command_gateway.record_operation_attempt(operation_id, command, payload, background=False)[0] is False
    with owned_postgres() as db:
        assert tuple(db.execute_native("SELECT payload_json,error_json FROM operation_receipts WHERE operation_id=%s", (operation_id,)).fetchone()) == (original_bytes, row[5])
        assert tuple(db.execute_native("SELECT payload_json,response_json FROM operation_receipts WHERE operation_id=%s", (complete_id,)).fetchone()) == (original_bytes, response_bytes)
        assert snapshot(db) == before


def test_gap_free_paste_keeps_preview_token_and_save_contract(monkeypatch):
    import hashlib
    import json
    import chess
    from app.services import analysis_paste as owner, canonical_prefix

    database = object()
    lines = [{"id": "root", "repertoire_id": "rep", "trained_color": "white",
              "start_fen": chess.STARTING_FEN, "moves_json": json.dumps(["e2e4"])}]
    monkeypatch.setattr(owner.storage, "repertoires", lambda db: [{"id": "rep", "name": "Example"}])
    monkeypatch.setattr(owner.storage, "lines", lambda db: lines)
    events = []
    monkeypatch.setattr(owner.storage, "insert_line", lambda *args: events.append("insert") or 1)
    monkeypatch.setattr(owner.storage, "initial_depth", lambda db: 2)
    monkeypatch.setattr(owner.storage, "set_line_depth", lambda *args: events.append("depth"))
    monkeypatch.setattr(canonical_prefix, "ensure_batch_lines_in_scope", lambda *args: ["route"])
    monkeypatch.setattr(canonical_prefix, "certify_admitted_route", lambda *args: events.append("certify"))
    text = "e4 e5 Nf3 *"
    preview = owner.build_paste_preview(database, text, None, None)
    signature = hashlib.sha256(json.dumps([[{"id": "rep", "name": "Example"}], lines], sort_keys=True).encode()).hexdigest()
    assert preview["preview_token"] == hashlib.sha256(json.dumps([text, None, None, signature], sort_keys=True).encode()).hexdigest()
    parsed = owner.parse_pasted_lines(text, None)
    result = owner.commit_pasted_lines(database, text, None, None, preview["preview_token"],
        [{"index": 0, "repertoire_id": "rep", "acknowledge_conflict": True}], preview, parsed)
    assert result == {"saved": [{"index": 0, "repertoire_id": "rep", "duplicate": False, "conflict": False}],
                      "affected_repertoire_ids": ["rep"], "gap_resolved": False}
    assert events == ["insert", "depth", "certify"]
