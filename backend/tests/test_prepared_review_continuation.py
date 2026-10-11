"""Bounded review handoff contracts; native locking is proved by the Docker suite."""
from datetime import date, datetime, timezone

import pytest

from app import database, main, review_commands
from test_queue_attempt_issuance import queued_attempts


def test_legacy_review_payload_omits_absent_prepared_identity_and_keeps_request_hash():
    from app.command_gateway import request_digest
    from app.models import ReviewRequest
    legacy = {"outcome": "correct", "guided": False, "queue_entry_id": 12, "recorded_at": None,
              "expected_review_id": None, "expected_revision": 1, "attempt_id": "legacy"}
    request = ReviewRequest.model_validate(legacy)
    assert request.durable_payload() == legacy
    assert request_digest("cards.review", {"card_id": "a", "review": request.durable_payload()}) == request_digest("cards.review", {"card_id": "a", "review": legacy})


class CommandConnection:
    def __init__(self, connection):
        self.connection = connection
        self.card_locks = []

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace(" FOR UPDATE", ""), parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute_native(self, statement, parameters=()):
        if "pg_advisory" in statement:
            self.card_locks.append(parameters)
            return self.connection.execute("SELECT 1")
        return self.execute(statement.replace("%s", "?"), parameters)


def payload(entries, **candidate_changes):
    candidate = {"queue_entry_id": entries["later-b"], "card_id": "later-b", "revision": 1,
                 "cycle": 0, "local_date": date.today().isoformat(), **candidate_changes}
    return {"card_id": "active-a", "review": {"outcome": "correct", "guided": False,
            "queue_entry_id": entries["active-a"], "expected_revision": 1,
            "attempt_id": "prepared-transition-a", "prepared_next": candidate}}


def test_prepared_continuation_replay_preserves_exactly_once_scheduling(queued_attempts, monkeypatch):
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    with database.connection() as connection:
        command = CommandConnection(connection)
        result = review_commands.submit_review(command, payload(queued_attempts))
        assert result["persisted"] is True
        assert result["prepared_continuation"] == {**payload(queued_attempts)["review"]["prepared_next"], "attempt_failed": False}
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 1
        replayed = review_commands.submit_review(command, payload(queued_attempts))
        assert replayed == result
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1


def test_legacy_receipt_confirmation_retains_authoritative_refresh(queued_attempts, monkeypatch):
    from app.models import ReviewRequest
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    recorded_at = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        main._apply_review("active-a", ReviewRequest(outcome="correct", queue_entry_id=queued_attempts["active-a"],
            recorded_at=recorded_at, expected_revision=1), database=connection)
        request_payload = payload(queued_attempts)
        request_payload["review"]["recorded_at"] = recorded_at
        result = review_commands.submit_review(CommandConnection(connection), request_payload)
        assert result["persisted"] and result["idempotent"]
        assert "prepared_continuation" not in result
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["revision", "cycle", "day", "promotion", "deleted", "archived", "buried", "absent"])
def test_prepared_continuation_requires_current_head_and_matching_identity(queued_attempts, monkeypatch, change):
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    request_payload = payload(queued_attempts)
    with database.connection() as connection:
        if change in {"revision", "cycle", "day"}:
            request_payload["review"]["prepared_next"][{"revision": "revision", "cycle": "cycle", "day": "local_date"}[change]] = "1999-01-01" if change == "day" else 2
        elif change == "promotion":
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'promoted-c',repertoire_id,kind,start_fen,moves_json,state,due_date,content_type FROM cards WHERE id='later-b'")
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'promoted-c',-1)", (date.today().isoformat(),))
        elif change == "deleted":
            connection.execute("INSERT INTO deleted_cards VALUES('later-b',?)", (date.today().isoformat(),))
        elif change == "archived":
            connection.execute("UPDATE cards SET archived=1 WHERE id='later-b'")
        elif change == "buried":
            connection.execute("UPDATE daily_queue SET status='buried' WHERE card_id='later-b'")
        else:
            del request_payload["review"]["prepared_next"]
        result = review_commands.submit_review(CommandConnection(connection), request_payload)
        assert result["persisted"] is True
        assert "prepared_continuation" not in result
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0
