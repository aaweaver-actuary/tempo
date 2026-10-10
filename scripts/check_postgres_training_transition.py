"""Synthetic, invocation-owned fixture for prepared-card contention measurements."""
import json
import re
import sys
from datetime import date
from uuid import uuid4

from check_postgres_daily_study_dispatch import configure, seed, cleanup, DSN, FEN
import psycopg


def main():
    action, identifier = sys.argv[1:3]
    if not re.fullmatch(r"training-transition-[a-f0-9-]+", identifier):
        raise ValueError("An invocation-owned synthetic identifier is required")
    configure()
    if action == "proof":
        sys.argv = [sys.argv[0], "seed-small", identifier, date.today().isoformat()]
        main()
        try:
            prove_optional_handoff(identifier)
        finally:
            cleanup(identifier)
        return
    if action == "cleanup":
        cleanup(identifier)
        return
    if action in {"seed", "seed-small"}:
        today = sys.argv[3]
        seed(identifier, request_refresh=action == "seed-small", queue_date=today)
        with psycopg.connect(DSN) as connection:
            for index in range(64):
                card_id = f"{identifier}-prepared-{index:03}"
                connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,introduced_at) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','learning','2000-01-01','white','2000-01-01')", (card_id, identifier, FEN))
                connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)", (identifier, card_id))
                connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,%s,'due',%s)", (today, card_id, index, identifier))
            connection.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (today,))
        return
    if action == "verify":
        with psycopg.connect(DSN) as connection:
            reviews = connection.execute("SELECT count(*) FROM reviews WHERE card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            complete = connection.execute("SELECT count(*) FROM opening_evidence_attempts WHERE state='complete' AND card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            events = connection.execute("SELECT count(*) FROM opening_evidence_events e JOIN opening_evidence_attempts a ON a.attempt_id=e.attempt_id WHERE a.state='complete' AND a.card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            pending = connection.execute("SELECT count(*) FROM background_tasks WHERE id LIKE %s AND state!='complete'", (identifier+'-analysis-%',)).fetchone()[0]
            print(json.dumps({"reviews": reviews, "complete_evidence": complete, "completion_events": events, "pending_maintenance": pending}))
        return
    raise ValueError("Unknown fixture action")


def prove_optional_handoff(identifier):
    """Real locks, post-review head validation and both durable receipt layers."""
    from app import postgres_store
    from app.command_gateway import execute_command, request_digest
    from app.models import ReviewRequest

    today = date.today().isoformat()
    with psycopg.connect(DSN) as connection:
        connection.execute("UPDATE daily_queue SET position=position-1000 WHERE card_id LIKE %s", (identifier+'-prepared-%',))
        entries = connection.execute("SELECT q.id,c.id,c.revision,q.cycle FROM daily_queue q JOIN cards c ON q.card_id=c.id WHERE c.id LIKE %s ORDER BY c.id", (identifier+'-prepared-%',)).fetchall()

    def identity(index):
        entry, card, revision, cycle = entries[index]
        return {"queue_entry_id": entry, "card_id": card, "revision": revision, "cycle": cycle, "local_date": today}

    def issue(index):
        item = identity(index)
        assert execute_command(str(uuid4()), "queue.issue_head", {"queue_date": today, "entry_id": item["queue_entry_id"],
            "card_id": item["card_id"], "expected_revision": item["revision"]})["issued"]

    def review(index, next_index=None):
        item = identity(index)
        request = ReviewRequest(outcome="correct", queue_entry_id=item["queue_entry_id"], expected_revision=item["revision"],
            attempt_id=f"{identifier[-36:]}-{index}", prepared_next=identity(next_index) if next_index is not None else None)
        return {"card_id": item["card_id"], "review": request.durable_payload()}

    issue(0)
    with psycopg.connect(DSN) as editing:
        editing.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{identity(1)['card_id']}",))
        result = execute_command(str(uuid4()), "cards.review", review(0, 1))
        assert result["persisted"] and "prepared_continuation" not in result
    issue(1)
    payload = review(1, 2)
    operation = str(uuid4())
    result = execute_command(operation, "cards.review", payload)
    assert result["prepared_continuation"] == {**identity(2), "attempt_failed": False}
    postgres_store.close_pools()
    assert execute_command(operation, "cards.review", payload) == result
    assert execute_command(str(uuid4()), "cards.review", payload) == result
    with psycopg.connect(DSN) as connection:
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id=%s", (identity(1)["card_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s", (identity(2)["queue_entry_id"],)).fetchone()[0] == 1
        connection.execute("UPDATE daily_queue SET position=-2000 WHERE id=%s", (identity(4)["queue_entry_id"],))
    assert "prepared_continuation" not in execute_command(str(uuid4()), "cards.review", review(2, 3))
    issue(4)
    legacy = review(4)
    assert "prepared_next" not in legacy["review"]
    old_shape = {"outcome": "correct", "guided": False, "queue_entry_id": identity(4)["queue_entry_id"], "recorded_at": None,
        "expected_review_id": None, "expected_revision": 1, "attempt_id": legacy["review"]["attempt_id"]}
    assert request_digest("cards.review", legacy) == request_digest("cards.review", {"card_id": identity(4)["card_id"], "review": old_shape})
    legacy_result = execute_command(str(uuid4()), "cards.review", legacy)
    assert legacy_result["persisted"] and "prepared_continuation" not in legacy_result
    print("prepared_continuation_native_lock_head_and_receipt_replay: passed")


if __name__ == "__main__":
    main()
