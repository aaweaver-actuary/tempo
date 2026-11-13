"""The current guard delegates exact attempt abandonment to its storage owner.

Former R11 presentation/conflict-schema precedence was retired with adaptive
recombination (SPEC §1); this guard does not recreate that subsystem.
"""

import pytest
from app.contracts.review_conflicts import ReviewConflict
from app.services import study_attempt_guard
from tempo_storage.lifecycle import queue_attempt_abandonment


def test_abandoned_original_attempt_is_rejected_with_exact_identity(monkeypatch):
    database = object()
    seen = []
    monkeypatch.setattr(queue_attempt_abandonment, "is_abandoned",
                        lambda *args: seen.append(args) or True)
    with pytest.raises(ReviewConflict) as rejected:
        study_attempt_guard.guard_current_attempt(database, 17, "original-card", 3)
    assert seen == [(database, 17, "original-card", 3)]
    assert rejected.value.code == "queue_attempt_retired"
    assert rejected.value.detail == "This attempt was abandoned. Refresh Study before continuing."


@pytest.mark.parametrize("entry,card,revision", [(17, "original-card", 3), (None, None, None)])
def test_non_abandoned_or_missing_identity_is_forwarded_without_new_conflict(monkeypatch, entry, card, revision):
    database = object()
    seen = []
    monkeypatch.setattr(queue_attempt_abandonment, "is_abandoned",
                        lambda *args: seen.append(args) or False)
    assert study_attempt_guard.guard_current_attempt(database, entry, card, revision) is None
    assert seen == [(database, entry, card, revision)]


def test_absent_abandonment_schema_does_not_query_receipt(monkeypatch):
    database = object()
    monkeypatch.setattr(queue_attempt_abandonment, "installed", lambda db: False if db is database else pytest.fail("wrong database"))
    assert study_attempt_guard.guard_current_attempt(database, 17, "original-card", 3) is None
