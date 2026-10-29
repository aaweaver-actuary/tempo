"""Private application over actual #502 plans; semantic in-memory owner fixture."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.study.recombination.application import Recombination, conservative_due
from app.study.recombination.models import CleanStreak, JoinMember
from app.study.recombination.ports import CurrentRoute, Obligation
from test_product_recombination_policy import REVIEW_TIME, context, member, review


class MemoryStore:
    def __init__(self):
        self.streaks, self.events, self.changes = {}, {}, {}
        self.busy = False

    def try_lock_route(self, route_id):
        return not self.busy

    def try_lock_evidence(self, identities):
        return not self.busy

    @staticmethod
    def key(identity):
        return identity.presentation_id, identity.presentation_revision, identity.evidence_epoch

    def streak(self, identity):
        return self.streaks.get(self.key(identity), CleanStreak(*self.key(identity)))

    def save_streak(self, state):
        self.streaks[self.key(state)] = state

    def event(self, event_id):
        return self.events.get(event_id)

    def save_event(self, event):
        self.events[event.event_id] = event

    def recent_completed(self, identity):
        seen_attempts, seen_reviews, unique = set(), set(), []
        for event in self.events.values():
            if not event.complete or event.source != "study":
                continue
            if event.attempt_id in seen_attempts or event.review_id in seen_reviews:
                continue
            seen_attempts.add(event.attempt_id)
            seen_reviews.add(event.review_id)
            if (event.presentation_id, event.presentation_revision, event.evidence_epoch) == self.key(identity):
                unique.append(event)
        return tuple(sorted(unique, key=lambda event: event.occurred_at, reverse=True)[:5])

    def receipt(self, command_id):
        value = self.changes.get(command_id)
        return value[1:] if value else None

    def presentation_exists(self, presentation_id):
        return any(result.presentation.presentation_id == presentation_id
            for change, _, _ in self.changes.values() for result in change.results)

    def save_change(self, change, digest, receipt):
        self.changes[change.command_id] = change, digest, receipt


class StudyFixture:
    """Explicit fake current owner, no production authority or FSRS claim."""
    def __init__(self):
        members = (member("A", 0, 1), member("B", 2, 3), member("C", 4, 5))
        self.current = CurrentRoute(context(), members, tuple((m.presentation.presentation_id,
            REVIEW_TIME + timedelta(days=20)) for m in members))
        self.saved = {}
        self.applied = []
        self.identities = {m.presentation.presentation_id for m in members}
        self.introduction_charges = 0

    def try_current(self, route_id):
        return self.current

    def saved_review(self, event):
        return self.saved.get(event.event_id) == event

    def identity_available(self, identifier):
        return identifier not in self.identities

    def apply(self, change):
        self.applied.append(change)
        retired = set(change.retired_uses)
        members = []
        for old in self.current.members:
            uses = tuple(use for use in old.route_uses if (old.presentation.presentation_id, use) not in retired)
            if uses:
                members.append(replace(old, route_uses=uses))
        for result in change.results:
            members.append(JoinMember(result.presentation, CleanStreak(result.presentation.presentation_id,
                result.presentation.revision, result.evidence_epoch), result.presentation.revision,
                (result.route_use,), result.starting_fen, result.ending_fen, result.moves, True))
            self.identities.add(result.presentation.presentation_id)
        dues = dict(self.current.due)
        dues.update((r.presentation.presentation_id, r.due_at) for r in change.results)
        self.current = replace(self.current, members=tuple(members), due=tuple(dues.items()),
            context=replace(self.current.context,
                expected_study_revision=self.current.context.expected_study_revision + 1))


def fixture(store=None, study=None):
    store, study = store or MemoryStore(), study or StudyFixture()
    application = Recombination(store, study, clock=lambda: REVIEW_TIME + timedelta(hours=1),
        next_local_study_day=lambda now: now.replace(hour=0, minute=0, second=0) + timedelta(days=1))
    return application, store, study


def earn(application, study, presentation_id, *, start=0, outcome="correct", count=3, epoch="initial"):
    for index in range(start, start + count):
        event = review(index, presentation_id, evidence_epoch=epoch, outcome=outcome)
        study.saved[event.event_id] = event
        application.record_review(study.current.context.route.route_id, event)


def prepared():
    app, store, study = fixture()
    for identifier in ("A", "B", "C"):
        earn(app, study, identifier)
    return app, store, study, app.propose("main-line").plan


def test_saved_identity_required_aggregate_counts_and_unknown_history_not_adopted():
    app, store, study = fixture()
    assert app.propose("main-line").plan is None  # fixture DTOs claim 3; no actual events
    with pytest.raises(ValueError, match="saved_review_unproved"):
        app.record_review("main-line", review(0))
    earn(app, study, "A", count=2)
    event = review(2, assistance="unknown")
    study.saved[event.event_id] = event
    assert app.record_review("main-line", event).count == 0


def test_ab_zero_no_early_chain_and_three_fresh_combined_reviews_allow_abc():
    app, store, study, plan = prepared()
    original_events = dict(store.events)
    receipt = app.apply_join(plan)
    assert receipt.status == "applied"
    combined = study.applied[-1].results[0]
    assert combined.presentation.clean_streak == 0 and combined.presentation.introduced
    assert study.introduction_charges == 0 and store.events == original_events
    assert app.propose("main-line").plan is None
    earn(app, study, combined.presentation.presentation_id, epoch=combined.evidence_epoch, start=10)
    next_plan = app.propose("main-line").plan
    assert next_plan.left.presentation_id == combined.presentation.presentation_id
    assert next_plan.right.presentation_id == "C"
    assert app.apply_join(next_plan).status == "applied"
    assert study.applied[-1].results[0].presentation.clean_streak == 0


@pytest.mark.parametrize("flag", ["active_attempt", "pending_attempt"])
def test_active_member_join_deferred_without_changes(flag):
    app, store, study, plan = prepared()
    study.current = replace(study.current, members=tuple(replace(m, **{flag:True})
        if m.presentation.presentation_id == "A" else m for m in study.current.members))
    assert app.apply_join(plan).status == "active_attempt"
    assert not store.changes and not study.applied


def test_recheck_revision_conflicts_membership_and_missing_authority():
    app, store, study, plan = prepared()
    study.current = replace(study.current, context=replace(study.current.context, expected_study_revision=11))
    assert app.apply_join(plan).status == "stale_revision"
    study.current = replace(study.current, context=replace(study.current.context, expected_study_revision=10,
        available=False))
    assert app.apply_join(plan).status == "ineligible"
    study.current = replace(study.current, context=replace(study.current.context, available=True),
        members=study.current.members[1:])
    assert app.apply_join(plan).status == "ineligible"
    study.try_current = lambda route_id: None
    assert app.apply_join(plan).reason == "current_authority_unavailable"
    assert not study.applied


def test_shared_memberships_retained_and_unrelated_attempt_unchanged():
    app, store, study, _ = prepared()
    a, b, c = study.current.members
    shared = replace(a.route_uses[0], route_id="other-branch", occurrence_id="other-use")
    unrelated = replace(c, active_attempt=True)
    study.current = replace(study.current, members=(replace(a, route_uses=a.route_uses + (shared,)), b, unrelated))
    plan = app.propose("main-line").plan
    assert app.apply_join(plan).status == "applied"
    kept = {m.presentation.presentation_id:m for m in study.current.members}
    assert kept["A"].route_uses == (shared,) and kept["C"] == unrelated
    assert study.applied[-1].retired_uses == (("A", a.route_uses[0]), ("B", b.route_uses[0]))


def test_retry_original_attempt_and_receipt_are_immutable():
    app, store, study, plan = prepared()
    event = review(2)
    assert app.record_review("main-line", event).count == 3
    before = len(store.events)
    renamed = replace(event, event_id="transport-retry")
    study.saved[renamed.event_id] = renamed
    assert app.record_review("main-line", renamed).count == 3
    receipt = app.apply_join(plan)
    assert app.apply_join(plan) == receipt and len(store.changes) == 1
    assert store.events[event.event_id].presentation_id == "A" and len(store.events) == before + 1
    with pytest.raises(ValueError, match="command_identity_conflict"):
        app.apply_join(plan.model_copy(update={"expected_study_revision":999}))
    assert len(study.applied) == 1


@pytest.mark.parametrize("outcome,assistance", [("hint","assisted"), ("wrong","unassisted"),
    ("again","unassisted"), ("guided","assisted")])
def test_failure_facts_reset_current_streak(outcome, assistance):
    app, store, study, plan = prepared()
    event = review(4, outcome=outcome, assistance=assistance)
    study.saved[event.event_id] = event
    assert app.record_review("main-line", event).count == 0
    assert app.apply_join(plan).status == "ineligible"


def test_obligation_identity_deadline_preserved_and_due_initialization_is_conservative():
    app, store, study, _ = prepared()
    owed = Obligation("original-logical-effect", "decision:A:0", REVIEW_TIME - timedelta(days=1))
    unrelated = Obligation("other-effect", "decision:C:4", REVIEW_TIME + timedelta(hours=2))
    study.current = replace(study.current, obligations=(owed, unrelated))
    plan = app.propose("main-line").plan
    assert plan.unresolved_obligation_ids == (owed.logical_id,)
    assert app.apply_join(plan).status == "applied"
    change = study.applied[-1]
    assert change.obligations == (owed,) and change.results[0].due_at == owed.available_by
    assert study.current.obligations == (owed, unrelated)  # no settlement or new lapse
    now = REVIEW_TIME
    assert conservative_due(now, now + timedelta(hours=16), (now+timedelta(hours=2),)) == now+timedelta(hours=2)
    assert conservative_due(now, now + timedelta(hours=16), (now+timedelta(days=20),)) == now+timedelta(hours=16)


def test_accepted_split_requires_fresh_streaks_and_retains_all_history():
    app, store, study, plan = prepared()
    app.apply_join(plan)
    combined = study.applied[-1].results[0]
    identifier = combined.presentation.presentation_id
    earn(app, study, identifier, start=10, outcome="again", epoch=combined.evidence_epoch)
    assert app.accept_split("main-line", identifier, 1, "split:1", confirmed=False).status == "ineligible"
    before = dict(store.events)
    receipt = app.accept_split("main-line", identifier, 1, "split:1", confirmed=True)
    assert receipt.status == "applied"
    change = study.applied[-1]
    assert len(change.results) == 2 and store.events == before
    assert all(r.presentation.clean_streak == 0 and r.presentation.introduced for r in change.results)
    assert all(identifier in r.presentation.lineage for r in change.results)
    assert change.results[0].moves + change.results[1].moves == combined.moves
    assert app.accept_split("main-line", identifier, 1, "split:1", confirmed=True) == receipt
    assert app.propose("main-line").plan is None
    for fragment in change.results:
        earn(app, study, fragment.presentation.presentation_id, start=20, epoch=fragment.evidence_epoch)
    rejoin = app.propose("main-line").plan
    assert rejoin is not None and rejoin.result.presentation_id != identifier
    assert app.apply_join(rejoin).status == "applied"
    assert len(store.changes) == 3 and all(e.presentation_id != rejoin.result.presentation_id for e in store.events.values())


def test_split_offer_uses_five_distinct_completed_attempts():
    app, store, study, plan = prepared()
    app.apply_join(plan)
    combined = study.applied[-1].results[0]
    name, epoch = combined.presentation.presentation_id, combined.evidence_epoch
    earn(app, study, name, start=10, outcome="again", count=2, epoch=epoch)
    original = study.saved[f"{name}:event:10"]
    for index in range(4):
        duplicate = replace(original, event_id=f"duplicate:{index}")
        study.saved[duplicate.event_id] = duplicate
        app.record_review("main-line", duplicate)
    assert app.accept_split("main-line", name, 1, "split:missing-failure", confirmed=True).status == "ineligible"
    earn(app, study, name, start=20, count=5, epoch=epoch)
    assert app.accept_split("main-line", name, 1, "split:old-failures", confirmed=True).status == "ineligible"


def test_scoped_owner_boundary_has_no_optional_producer_import_or_cycle():
    from test_product_contracts import boundary_errors, has_import_cycle, import_targets
    directory = Path(__file__).parents[1] / "app/study/recombination"
    sources = [("study", path.read_text(), "app.study.recombination." + path.stem)
        for path in directory.glob("*.py")]
    assert not boundary_errors(sources)
    assert not has_import_cycle({module:source for _, source, module in sources})
    for _, source, module in sources:
        assert not any(name.startswith(("app.analysis", "app.product_composition", "tempo_intelligence",
            "psycopg", "tempo_legacy_database")) for name in import_targets(source, module))


def test_invalid_day_boundary_or_unproved_route_defers_without_writes():
    app, store, study, plan = prepared()
    app.next_day = lambda now: now + timedelta(days=2)
    with pytest.raises(ValueError, match="within 26 hours"):
        app.apply_join(plan)
    assert not study.applied and not store.changes
    study.current = replace(study.current, context=replace(study.current.context,
        route=replace(study.current.context.route, route_id="different-route")))
    assert app.apply_join(plan).reason == "current_authority_unavailable"


def test_changed_event_identity_rejected_and_busy_route_has_no_effect():
    app, store, study, plan = prepared()
    with pytest.raises(ValueError, match="event_identity_conflict"):
        app.record_review("main-line", replace(review(2), outcome="again"))
    store.busy = True
    assert app.apply_join(plan).reason == "route_busy"
    assert app.propose("main-line") is None
    assert not study.applied
