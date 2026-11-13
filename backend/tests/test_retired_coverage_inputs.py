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
