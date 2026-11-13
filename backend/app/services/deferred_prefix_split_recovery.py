"""Bounded readiness check for an accepted Study prefix split.

The existing foreground-operation recovery beat calls this at most once per
pass. The intent is durable; this function only publishes its child command.
"""
import json

from tempo_storage.connections import connection
from tempo_storage.commands.receipts import storage as receipt_storage
from tempo_storage.study import deferred_prefix_splits as storage
from tempo_storage.authored_repertoires.opening_mutations import prefix_splits as split_storage
from app.contracts.command_identity import request_digest


def valid_original_review_receipt(row):
    try:
        value = json.loads(row["result_json"])
    except (TypeError, ValueError):
        return False
    return isinstance(value, dict) and value.get("persisted") is True and value.get("review_id") == row["review_id"]


def enqueue_one_ready_split() -> bool:
    with connection(background=True) as database:
        candidate = storage.due_intent(database)
        if candidate is None:
            return False
        disposition, card = storage.try_lock_source(database, candidate["card_id"])
        if disposition == "busy":
            storage.delay_check_if_unlocked(database, candidate["intent_id"])
            return False
        if disposition == "missing":
            intent = storage.intent(database, candidate["intent_id"], lock=True)
            if intent is not None and intent["state"] in ("waiting_review", "ready"):
                storage.set_held(database, intent["intent_id"], "source_missing")
                return True
            return False
        intent = storage.intent(database, candidate["intent_id"], lock=True)
        if intent is None or intent["state"] not in ("waiting_review", "ready"):
            return False
        if intent["state"] == "ready":
            child = receipt_storage.foreground_operation_state(database, intent["child_operation_id"])
            if child is None or child["command_name"] != "cards.prefix_split.apply_deferred":
                storage.set_held(database, intent["intent_id"], "child_receipt_unavailable")
                return True
            if child["state"] in ("failed", "blocked", "complete"):
                detail = child["error_json"] or child["last_error_json"]
                try:
                    error = json.loads(detail) if detail else {}
                except (TypeError, ValueError):
                    error = {}
                message = error.get("message") if isinstance(error, dict) else None
                reason = "child_" + child["state"] + ((": " + str(message)[:512]) if message else "")
                storage.set_held(database, intent["intent_id"], reason)
                return True
            storage.delay_check(database, intent["intent_id"])
            return False
        if card["revision"] != intent["source_revision"]:
            storage.set_held(database, intent["intent_id"], "source_changed")
            return True
        matches = storage.matching_reviews(database, intent["card_id"], intent["source_revision"], intent["queue_entry_id"])
        if len(matches) > 1 or (matches and not valid_original_review_receipt(matches[0])):
            storage.set_held(database, intent["intent_id"], "original_review_ambiguous")
            return True
        if not matches or split_storage.has_active_issued_attempt(database, intent["card_id"]):
            storage.delay_check(database, intent["intent_id"])
            return False
        child = "prefix-split-intent:" + intent["intent_id"]
        payload = {"intent_id": intent["intent_id"]}
        command = "cards.prefix_split.apply_deferred"
        digest = request_digest(command, payload)
        enqueued = receipt_storage.enqueue_foreground_operation(database, child, command, digest, payload)
        if enqueued is None:
            storage.delay_check(database, intent["intent_id"])
            return False
        if not enqueued:
            storage.set_held(database, intent["intent_id"], "child_identity_conflict")
            return True
        if not storage.set_ready(database, intent["intent_id"], matches[0]["attempt_id"], matches[0]["review_id"], child):
            raise RuntimeError("Split intent changed while publishing recovery")
        return True
