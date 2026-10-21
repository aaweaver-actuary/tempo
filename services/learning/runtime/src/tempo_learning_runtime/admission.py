"""One Learning admission writer; deterministic published route order from #235.

Preparation suggests one card. This writer rechecks active definitions, exact
prerequisites, quota, deduplication and frozen identity under card/queue locks.
"""

import json
from .clock import date
from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
from . import frozen_opening_order
from .real_game_feedback import MISS_REASON, has_outstanding_real_game_miss
from tempo_learning_database.adapters import admission as storage


lock_card = storage.lock_card
protected_queue_ids = storage.protected_queue_ids


def admit(database, card_id, queue_day, reason=None):
    lock_card(database, card_id)
    lock_queue_date_for_position(database, queue_day)
    return admit_prelocked(database, card_id, queue_day, reason)


def admit_prelocked(database, card_id, queue_day, reason=None, *, proposal=None):
    """The sole writer after encompassing card/queue locks; no lock callbacks."""
    card = storage.card(database, card_id)
    if (
        not card
        or not card["serving_active"]
        or card["archived"]
        or card["superseded_by"]
        or card["pending_validation"]
    ):
        return False
    if storage.deleted_card(database, card_id):
        return False
    if reason == MISS_REASON:
        if not has_outstanding_real_game_miss(database, card_id):
            return False
        if proposal and (
            card["revision"],
            card["publication_id"],
            card["publication_generation"],
        ) != (
            proposal.card_revision,
            proposal.publication_id,
            proposal.publication_generation,
        ):
            return False
    if card["due_date"] > queue_day and reason is None:
        return False
    if card["state"] == "locked" and reason != MISS_REASON:
        return False
    if (
        card["content_type"] == "defense"
        and not storage.defense_enabled(database)
    ):
        return False
    if card["content_type"] == "study_exercise":
        eligible = storage.eligible_study_exercise(database, card["study_exercise_id"], queue_day)
        if not eligible:
            return False
    if card["content_type"] == "opening":
        # Current published occurrences, never scores, provide ordinary introductions.
        route = storage.opening_route(database, card_id, int(reason == MISS_REASON), proposal.route_id if proposal else "")
        if not route:
            return False
        membership = route[0]
        planned = frozen_opening_order.annotate(
            database, [{"card_id": card_id, "repertoire_id": membership}]
        )[0]
        if not frozen_opening_order.proposal_valid(database, planned):
            return False
    else:
        membership = card["repertoire_id"]
    if reason == MISS_REASON:
        if storage.buried_entry(database, card_id, queue_day):
            return False
        from .review_service import ensure_card_queued_after

        protected = protected_queue_ids(database, queue_day)
        if any(
            row[0] in protected
            for row in storage.queued_entry_ids(database, card_id, queue_day)
        ):
            return False

        ensure_card_queued_after(
            database,
            card_id,
            after_cards=0,
            attempt_state="gameplay",
            priority_reason=reason,
            queue_date=queue_day,
            protected_ids=protected,
            preserve_order=False,
        )
        storage.retain_admission_membership(database, membership, card_id, queue_day)
        storage.introduce_missed_card(database, queue_day, card_id)
        return True
    if storage.existing_entry(database, card_id, queue_day):
        return False
    new = card["introduced_at"] is None
    if new:
        policy = storage.admission_settings(database)
        if card["content_type"] == "opening":
            limit = storage.opening_limit(database, policy["new_cards_per_day"], membership)
            # Origins retain quota even if today's projection/content eligibility changes.
            used = storage.used_opening_quota(database, queue_day, membership)
        else:
            limit = (
                policy["study_new_per_day"]
                if card["content_type"] == "study_exercise"
                else policy["tactic_new_per_day"]
                if card["content_type"] == "tactic"
                else policy["new_cards_per_day"]
            )
            used = storage.used_family_quota(database, queue_day, card["content_type"])
        if used >= limit and reason != MISS_REASON:
            return False
    maximum = storage.next_position(database, queue_day)
    storage.insert_entry(database, queue_day, card_id, maximum, card["content_type"], "new" if new else "review", membership, reason)
    storage.introduce_card(database, queue_day, card_id)
    return True


next_candidates = storage.next_candidates
