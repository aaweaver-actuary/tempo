"""Source vertical fixture: real legacy queue/review writer, scripted module SQL.

This is not PostgreSQL durability/locking proof. The script supplies fixed owner
rows and emulates module SQL effects; qualifying outcome logic executes in the
real installed-library source. Native execution remains a separate finite gate.
"""

from contextlib import nullcontext, asynccontextmanager
import asyncio

import pytest

from tempo_legacy_database import connections as database


_PRIORITY_POSITION_SQL = """UPDATE daily_queue queue SET position=planned.position
        FROM unnest(%s::bigint[],%s::bigint[],%s::bigint[])
            AS planned(id,previous,position)
        WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'
          AND queue.position=planned.previous
          AND queue.review_result_json IS NULL
          AND queue.admission_kind IS DISTINCT FROM 'explicit'
          AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins origin
              WHERE origin.queue_entry_id=queue.id AND origin.card_id=queue.card_id
                AND origin.revision=(SELECT revision FROM cards WHERE id=queue.card_id)
                AND origin.issued_as_head=1)
          AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
              WHERE attempt.queue_entry_id=queue.id)"""
_STAMP_ISSUED_HEAD_SQL = """UPDATE queue_attempt_origins SET
            issuance_sequence=CASE WHEN issued_as_head=0
                THEN nextval('study_attempt_issuance_sequence') ELSE issuance_sequence END,
            issued_at=CASE WHEN issued_as_head=0 THEN %s ELSE issued_at END,
            issued_as_head=1
            WHERE queue_entry_id=%s AND card_id=%s AND revision=%s"""


@pytest.mark.parametrize(
    "method,path,work_class,status",
    [
        ("POST", "/api/game-evidence/product/feedback", "background", 200),
        ("GET", "/api/game-evidence/product/effect", "background", 200),
    ],
)
def test_http_cutover_matching_background_admission_and_query_only_guard_are_preserved(
    monkeypatch, method, path, work_class, status
):
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from app import main
    from tempo_legacy_database.adapters import postgres_store

    entered = []

    @asynccontextmanager
    async def foreground():
        entered.append("foreground")
        yield

    def background():
        entered.append("background")
        return nullcontext()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "async_foreground", foreground)
    monkeypatch.setattr(main.activity_gate, "background_request", background)
    request = Request(
        dict(
            type="http",
            method=method,
            path=path,
            raw_path=path.encode(),
            query_string=b"",
            scheme="http",
            headers=[],
            server=("test", 80),
            client=("test", 1),
            http_version="1.1",
        )
    )

    async def next_handler(_request):
        assert database._query_only_request.get() == (method == "GET")
        return JSONResponse({"dispatched": False})

    response = asyncio.run(main.prioritize_foreground_requests(request, next_handler))
    assert response.status_code == status and entered == (
        [work_class] if work_class else []
    )
    assert not database._query_only_request.get()
