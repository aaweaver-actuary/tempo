"""Deterministic queue membership and mixing over captured rows."""

from __future__ import annotations

import hashlib
import random
from typing import Sequence, TypedDict

from .real_game_feedback import MISS_REASON


class QueueOrderingRow(TypedDict):
    id: int
    card_id: str
    position: int
    content_type: str | None
    admission_kind: str
    gameplay_priority_reason: str | None


class SavedQueueOrder(TypedDict):
    seed: int
    membership_hash: str


QueueOrderPlan = tuple[int, str, list[QueueOrderingRow]]


def _queue_membership_hash(rows: Sequence[QueueOrderingRow]) -> str:
    return hashlib.sha256(
        ("queue-mix-v2\0" + "\0".join(
            f"{row['id']}:{row['card_id']}" for row in rows
        )).encode()
    ).hexdigest()


def _plan_daily_queue_order(rows: Sequence[QueueOrderingRow], day: str, saved: SavedQueueOrder | None, *, preserve_through_entry_id: int | None = None) -> QueueOrderPlan:
    """Compute the stable queue mix after its database read has closed."""

    membership_hash = _queue_membership_hash(rows)
    seed = (
        saved["seed"]
        if saved
        else int(hashlib.sha256(day.encode()).hexdigest()[:15], 16)
    )
    explicit_rows = [row for row in rows if row["admission_kind"] == "explicit"]
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        if row["admission_kind"] == "explicit":
            continue
        bucket = row["content_type"] or "opening"
        groups.setdefault((row["admission_kind"], bucket), []).append(row)
    for key, values in groups.items():
        group_seed = int(
            hashlib.sha256(f"{seed}:{key[0]}:{key[1]}".encode()).hexdigest()[:15],
            16,
        )
        random.Random(group_seed).shuffle(values)
    category_order = ["opening", "tactic", "defense", "endgame", "middlegame", "study_exercise"]
    category_offset = seed % len(category_order)
    category_order = category_order[category_offset:] + category_order[:category_offset]
    cohort_order = ["review", "new"] if seed % 2 == 0 else ["new", "review"]
    category_cursors = {cohort: 0 for cohort in cohort_order}
    ordered = list(explicit_rows)
    while len(ordered) < len(rows):
        progressed = False
        for cohort in cohort_order:
            available_categories = [
                category
                for category in category_order
                if groups.get((cohort, category))
            ]
            if not available_categories:
                continue
            cursor = category_cursors[cohort] % len(available_categories)
            category = available_categories[cursor]
            category_cursors[cohort] += 1
            ordered.append(groups[(cohort, category)].pop())
            progressed = True
        if not progressed:
            break
    prioritized_misses = [row for row in ordered if row["gameplay_priority_reason"] == MISS_REASON]
    if prioritized_misses:
        ordinary_cards = [row for row in ordered if row["gameplay_priority_reason"] != MISS_REASON]
        ordered = sorted(prioritized_misses, key=lambda row: (row["card_id"], row["id"])) + ordinary_cards
    if preserve_through_entry_id is not None:
        # A review can extend today's queue, but cannot move a surviving attempt.
        preserved_rows = sorted(
            (row for row in rows if row["id"] <= preserve_through_entry_id),
            key=lambda row: (row["position"], row["id"]),
        )
        ordered = preserved_rows + [row for row in ordered if row["id"] > preserve_through_entry_id]
    return seed, membership_hash, ordered

