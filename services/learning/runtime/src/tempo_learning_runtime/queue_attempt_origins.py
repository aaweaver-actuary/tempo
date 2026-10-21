from __future__ import annotations
from tempo_learning_database.adapters import queue_attempt_origins as storage


def recover_queue_entry(database, card_id: str, request) -> dict:
    from .review_conflicts import ReviewConflict

    if not request.attempt_id or not request.queue_entry_id:
        raise ReviewConflict(
            "queue_attempt_unprovable",
            "The original queue attempt cannot be verified. The completed result needs review.",
        )
    contexts = storage.recovery_origins(database, request.queue_entry_id, card_id)
    if request.expected_revision is not None:
        contexts = [
            context
            for context in contexts
            if context["revision"] == request.expected_revision
        ]
    if len(contexts) != 1:
        raise ReviewConflict(
            "queue_attempt_unprovable",
            "The original queue attempt cannot be verified. The completed result needs review.",
        )
    origin = dict(contexts[0])
    current = storage.current_card_definition(database, card_id)
    if (
        not current
        or any(
            current[column] != origin[column]
            for column in (
                "revision",
                "start_fen",
                "moves_json",
                "trained_color",
                "content_type",
            )
        )
        or (
            origin["legacy"]
            and request.expected_revision is None
            and origin["revision"] != 1
        )
    ):
        raise ReviewConflict(
            "card_revision_changed",
            "The card changed after this attempt. The completed result needs review.",
        )
    if (
        origin["last_status"] in {"buried", "complete"}
        and not origin["review_result_json"]
    ):
        raise ReviewConflict(
            "queue_attempt_retired",
            "This queue attempt was retired without a saved review. The completed result needs review.",
        )
    return {
        **origin,
        "id": origin["queue_entry_id"],
        "status": "complete" if origin["review_result_json"] else "queued",
        "_recovered": True,
    }


retain_recovered_completion = storage.retain_recovered_completion


def validate_failure_marker(
    database, entry_id: int, card_id=None, expected_revision=None
) -> None:
    """A queue-only legacy marker cannot choose between replacement contexts."""
    from .review_conflicts import ReviewConflict

    contexts, current = storage.failure_marker_facts(database, entry_id)
    if card_id is not None:
        contexts = [
            origin
            for origin in contexts
            if origin["card_id"] == card_id
            and (expected_revision is None or origin["revision"] == expected_revision)
        ]
    if (
        not current
        or len(contexts) != 1
        or current["id"] != contexts[0]["card_id"]
        or any(
            current[column] != contexts[0][column]
            for column in (
                "revision",
                "start_fen",
                "moves_json",
                "trained_color",
                "content_type",
            )
        )
    ):
        raise ReviewConflict(
            "queue_attempt_unprovable",
            "The guided attempt no longer matches this queue entry. Its completed review needs reconciliation.",
        )
