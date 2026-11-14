"""Disposable PostgreSQL proof for retained Paste command registration."""

import json
import os
from urllib.parse import urlparse

import chess
import pytest


def test_retained_paste_command_commits_once_and_fences_stale_preview(monkeypatch):
    dsn = os.environ.get("TEMPO_W04_PASTE_DSN")
    if not dsn:
        pytest.skip("owned Paste proof database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w04_paste":
        raise ValueError("refusing non-W04 database")
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", dsn)
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", dsn)
    from tempo_storage import postgres

    from app import analysis_paste_commands, command_gateway
    from app.contracts.command_registry import RETIRED_COMMANDS
    from app.services.analysis_paste import build_paste_preview

    assert "analysis.paste.commit" not in RETIRED_COMMANDS
    assert (
        command_gateway._handlers["analysis.paste.commit"]
        is analysis_paste_commands.commit_analysis_paste
    )
    with postgres.connection() as db:
        db.execute("INSERT INTO settings(id) VALUES(1)")
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('paste-proof','fixture','fixture','2026-01-01')"
        )
        db.execute(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('root','paste-proof','root','white',?,?,'2026-01-01')",
            (chess.STARTING_FEN, json.dumps(["e2e4"])),
        )
    with postgres.connection() as db:
        db.execute(
            "INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status) VALUES('paste-proof','clean','idle') ON CONFLICT(repertoire_id) DO UPDATE SET status='clean'"
        )
    text = "1. e4 e5 2. Nf3 *"
    with postgres.connection(read_only=True) as db:
        preview = build_paste_preview(db, text, None, None)
    request = {
        "text": text,
        "preview_token": preview["preview_token"],
        "selections": [
            {"index": 0, "repertoire_id": "paste-proof", "acknowledge_conflict": True}
        ],
    }
    payload = {"request": request, "prepared_preview": preview}
    result = command_gateway.execute_command(
        "paste-proof-operation", "analysis.paste.commit", payload
    )
    assert result and result["affected_repertoire_ids"] == ["paste-proof"]
    assert (
        command_gateway.read_operation("paste-proof-operation")["state"] == "complete"
    )
    assert (
        command_gateway.execute_command(
            "paste-proof-operation", "analysis.paste.commit", payload
        )
        == result
    )
    with postgres.connection(read_only=True) as db:
        assert (
            db.execute(
                "SELECT status FROM repertoire_integrity_state WHERE repertoire_id='paste-proof'"
            ).fetchone()[0]
            == "unchecked"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM repertoire_lines WHERE repertoire_id='paste-proof'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT count(*) FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key='paste-proof'"
            ).fetchone()[0]
            == 1
        )
    # Original preview no longer describes the current source; a new operation
    # using it must fail without adding another line or graph generation.
    assert (
        command_gateway.execute_command(
            "paste-proof-stale", "analysis.paste.commit", payload
        )
        is None
    )
    assert (
        command_gateway.read_operation("paste-proof-stale")["error"]["status_code"]
        == 409
    )
    with postgres.connection(read_only=True) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM repertoire_lines WHERE repertoire_id='paste-proof'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT count(*) FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key='paste-proof'"
            ).fetchone()[0]
            == 1
        )
    postgres.close_pools()
