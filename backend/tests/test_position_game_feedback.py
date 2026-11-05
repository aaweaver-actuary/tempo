"""Position settlement requires the actual saved target response and reset version."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from app.contracts.product_v02.learning import GameLapse
from app.study.game_feedback import Completion
from tempo_legacy_database.adapters.study import position_grade_effects as grades
from test_product_game_lapse import (
    CARD,
    NOW,
    Decisions,
    MemoryStore,
    fixture,
    issued_attempt,
    encounter_owner,
)
from app.study.game_feedback import GameFeedback


def test_position_settlement_requires_owner_proof_and_post_reset_issuance():
    store = MemoryStore()
    service = GameFeedback(store, Decisions(), clock=lambda: NOW)
    lapse = fixture("lapse", GameLapse)
    service.accept_lapse(lapse)
    completion = Completion(
        100,
        CARD,
        1,
        NOW + timedelta(hours=1),
        False,
        "position_study",
        issued_attempt(CARD, 1, 8, NOW + timedelta(minutes=1)),
    )
    # Aggregate correctness and a source-kind string alone provide no authority.
    service.settle(completion)
    assert store.get(lapse.key).status == "applied"
    store.position_completion_matches = lambda effect, completion: True
    service.settle(replace(completion, issuance=issued_attempt(CARD, 1, 7, NOW)))
    assert store.get(lapse.key).status == "applied"
    service.settle(completion)
    assert store.get(lapse.key).status == "settled"


class Result:
    def __init__(self, data):
        self.data = data

    def fetchone(self):
        return self.data

    def fetchall(self):
        return self.data


class Database:
    def __init__(self, reset):
        self.reset = reset

    def execute_native(self, sql, args):
        if "review_attempt_receipts" in sql:
            return Result(
                dict(attempt_id="a", queue_entry_id=1, source_digest="digest")
            )
        if "study_position_first_responses" in sql:
            return Result([dict(event_id="event")])
        if "source_kind='study'" in sql:
            return Result(dict(grade="observe"))
        if "source_kind='game_lapse'" in sql:
            return Result(self.reset)
        raise AssertionError(sql)


@pytest.mark.parametrize(
    "assisted,correct,disposition,version,expected",
    [
        (False, True, "expected", 4, True),
        (True, True, "expected", 4, False),
        (False, False, "wrong", 4, False),
        (False, True, "expected", 3, False),
    ],
)
def test_position_response_must_be_unassisted_exact_and_after_reset(
    monkeypatch, assisted, correct, disposition, version, expected
):
    key = NS(repertoire_id="rep", variant="standard", position="fen")
    definition = NS(key=key, expected_uci="e2e4")
    plan = NS(
        attempt_id="a",
        due_positions=(NS(definition=definition, position_revision=version),),
    )
    response = NS(
        definition=definition,
        assistance_before=assisted,
        manual_failure=False,
        correct=correct,
        event_id="event",
    )
    monkeypatch.setattr(grades, "_load_plan", lambda *a: plan)
    monkeypatch.setattr(grades, "_completion_saved", lambda *a: True)
    monkeypatch.setattr(
        grades, "_load_events", lambda *a: ((response, disposition, "digest", NOW),)
    )
    effect = NS(
        qualification=None,
        lapse=NS(
            finding=NS(repertoire_id="rep", position_key="fen", expected_uci=("e2e4",))
        ),
        key=NS(logical_id=lambda: "game-key"),
    )
    completion = NS(review_id=1, card_id="card", revision=1)
    db = Database(
        dict(version_after=4, repertoire_id="rep", variant="standard", position="fen")
    )
    assert grades.position_completion_matches(db, effect, completion) is expected


def test_qualified_intake_applies_position_once_before_receipt(encounter_owner):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))

    class PositionIntake:
        calls = 0

        def position_introduced(self, command, qualification):
            return True

        def fence_position_intake(self, command, qualification):
            assert not store.effects
            return "position"

        def apply_position_lapse(self, key, *, day, now):
            self.calls += 1
            assert store.get(key).status == "pending"
            return "position-receipt", now, 2

    port = PositionIntake()
    service.position_intake = port
    receipt = service.accept_qualified(
        command, qualification=qualification_for(command)
    )
    assert receipt.progress.position_grade_effect_id == "position-receipt"
    assert receipt.progress.scheduling_review_id is None
    assert receipt.progress.remediation_fence is not None
    assert not store.reviews and port.calls == 1
    assert service.refresh_encounter(command.key).reason == "position_priority_pending"
    assert not store.queue
    # Retry traverses the same pre-lock fence and returns the accepted receipt.
    port.fence_position_intake = lambda command, qualification: "position"
    replay = service.accept_qualified(command, qualification=qualification_for(command))
    assert replay.outcome == "duplicate" and port.calls == 1


def test_unseen_position_holds_for_canonical_position_introduction(encounter_owner):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    service.position_intake = NS(
        fence_position_intake=lambda *args: "position",
        position_introduced=lambda *args: False,
    )
    receipt = service.accept_qualified(
        command, qualification=qualification_for(command)
    )
    assert receipt.reason == "position_introduction_pending"
    assert (
        service.refresh_encounter(command.key).reason == "position_introduction_pending"
    )
    assert not store.reviews


def test_game_grade_writes_position_again_with_original_game_identity(monkeypatch):
    from fsrs import Card
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey
    from tempo_legacy_database.adapters.authored_repertoires import (
        position_study_source,
    )
    from tempo_legacy_database.adapters.study import (
        game_feedback as feedback_storage,
        position_owner_epoch,
    )

    command, qualification, position = departure(LONDON_ORDER_A)
    definition = PositionDefinition(
        PositionKey("london", position), "g1f3", "london-policy"
    )
    source = NS(
        authored_token=NS(repertoire_id="london", authored_digest="source"),
        routes=(NS(decisions=(NS(definition=definition),)),),
    )
    effect = NS(
        status="pending",
        control=None,
        qualification=qualification,
        disposition="introduced_again",
        encounter=command,
        lapse=None,
    )
    monkeypatch.setattr(
        position_study_source, "fence_position_source", lambda *args: None
    )
    monkeypatch.setattr(
        position_owner_epoch,
        "owner_mode",
        lambda *args: NS(disposition="position", epoch="epoch"),
    )
    monkeypatch.setattr(
        feedback_storage,
        "PostgresGameFeedbackStore",
        lambda db: NS(get=lambda key: effect),
    )
    card = Card(
        card_id=123,
        due=NOW,
        last_review=NOW - timedelta(days=1),
        stability=10,
        difficulty=5,
    )
    state = dict(
        expected_uci="g1f3",
        policy_version="london-policy",
        version=3,
        fsrs_internal_card_id=123,
        fsrs_json=card.to_json(),
    )
    monkeypatch.setattr(
        grades, "_lock_states", lambda *args, **kwargs: {definition.key: state}
    )

    class Writer:
        read_only = False
        raw = NS(info=NS(transaction_status=NS(name="INTRANS")))

        def __init__(self):
            self.writes, self.prior = [], None

        def execute_native(self, sql, args=()):
            if "SELECT effect_id,request_digest" in sql:
                return Result(self.prior)
            if "SELECT introduced_at" in sql:
                return Result(dict(introduced_at=NOW - timedelta(days=5)))
            self.writes.append((sql, args))
            if "INSERT INTO study_position_grades" in sql:
                self.prior = dict(
                    effect_id=args[0],
                    request_digest=args[11],
                    accepted_at=args[12],
                    version_after=args[-1],
                )
            return Result(None)

    db = Writer()
    receipt, accepted, version = grades.apply_game_lapse(
        db,
        command.key,
        prepared_source=source,
        accepted_study_day=NOW.date(),
        accepted_at=NOW,
    )
    assert accepted == NOW and version == 4 and len(receipt) == 64
    assert len(db.writes) == 2
    sql, args = db.writes[1]
    assert "'game_lapse'" in sql and args[1] == command.key.logical_id()
    assert "reviews" not in sql and args[-2:] == (3, 4)
    replay = grades.apply_game_lapse(
        db,
        command.key,
        prepared_source=source,
        accepted_study_day=NOW.date(),
        accepted_at=NOW + timedelta(hours=1),
    )
    assert replay == (receipt, NOW, 4) and len(db.writes) == 2


def test_position_urgency_reads_exact_durable_reset_and_holds_changed_state(
    monkeypatch,
):
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import Hold
    from tempo_legacy_database.adapters.study import game_feedback as storage

    command, qualification, position = departure(LONDON_ORDER_A)
    progress = NS(
        position_grade_effect_id="grade",
        remediation_fence=object(),
        target=NS(card_id="card", card_revision=1),
        stream="forward",
        determined_at=NOW,
    )
    effect = NS(
        control=None,
        qualification=qualification,
        progress=progress,
        encounter=command,
        lapse=None,
        key=command.key,
    )
    monkeypatch.setattr(storage, "installed", lambda db: True)
    monkeypatch.setattr(
        storage.PostgresGameFeedbackStore, "_effect", lambda row: effect
    )
    row = dict(
        effect_id="grade",
        repertoire_id="london",
        variant="standard",
        position=position,
        owner_epoch="epoch",
        source_digest="digest",
        expected_uci="g1f3",
        policy_version="london-policy",
        version_after=4,
        state_epoch="epoch",
        state_digest="digest",
        state_uci="g1f3",
        state_policy="london-policy",
        state_version=4,
        introduced_at=NOW,
    )

    class Reader:
        def execute_native(self, sql, args=()):
            if "SELECT * FROM study_game_feedback" in sql:
                return Result([{}])
            if "pg_try_advisory" in sql:
                return Result((True,))
            if "FROM study_position_grades" in sql:
                return Result(row)
            raise AssertionError(sql)

    db = Reader()
    fact = storage.read_position_urgency(
        db, card_id="card", revision=1, repertoire_id="london"
    )
    assert fact.definition.key.position == position and fact.position_version == 4
    assert fact.grade_effect_id == "grade"
    assert (
        storage.read_position_urgency(
            db, card_id="card", revision=1, repertoire_id="london", lock=True
        )
        == fact
    )
    row["state_digest"] = "changed"
    assert isinstance(
        storage.read_position_urgency(
            db, card_id="card", revision=1, repertoire_id="london"
        ),
        Hold,
    )

    class TooMany:
        def execute_native(self, sql, args=()):
            assert "LIMIT 129" in sql
            return Result([{}] * 129)

    assert (
        storage.read_position_urgency(
            TooMany(), card_id="card", revision=1, repertoire_id="london"
        ).reason
        == "position_urgency_candidate_limit"
    )


def test_unseen_feedback_calls_targeted_admission_once(encounter_owner, monkeypatch):
    from test_product_game_lapse import qualification_for
    from app.services import position_queue_admission

    store, _, _, _, service, command = encounter_owner
    store.database = object()
    service.position_intake = NS(
        fence_position_intake=lambda *args: "position",
        position_introduced=lambda *args: False,
    )
    qualification = qualification_for(command)
    service.accept_qualified(command, qualification=qualification)
    calls = []

    def admit(database, prepared, fence, *, day, now, introductions):
        calls.append((database, prepared, fence, day))
        return NS(status="introduced")

    monkeypatch.setattr(
        position_queue_admission, "admit_targeted_position_introduction", admit
    )
    target = (object(), command.decision)
    receipt = service.refresh_encounter(
        command.key,
        qualification=qualification,
        position_introduction=target,
        position_fence="fence",
    )
    assert receipt.reason == "position_priority_pending"
    assert receipt.progress.introduction_applied_at is not None
    assert receipt.progress.scheduling_review_id is None and not store.reviews
    service.refresh_encounter(
        command.key,
        qualification=qualification,
        position_introduction=target,
        position_fence="fence",
    )
    assert len(calls) == 1


def test_position_urgency_requires_real_same_transaction_introduction(monkeypatch):
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import Hold
    from tempo_legacy_database.adapters.study import game_feedback as storage

    command, qualification, position = departure(LONDON_ORDER_A)
    progress = NS(
        position_grade_effect_id=None,
        introduction_applied_at=NOW.isoformat(),
        remediation_fence=object(),
        target=NS(card_id="card", card_revision=1),
        stream="forward",
        determined_at=NOW,
    )
    effect = NS(
        control=None,
        qualification=qualification,
        progress=progress,
        disposition="first_introduction",
        encounter=command,
        lapse=None,
        key=command.key,
    )
    monkeypatch.setattr(storage, "installed", lambda db: True)
    monkeypatch.setattr(
        storage.PostgresGameFeedbackStore, "_effect", lambda row: effect
    )
    state = dict(
        repertoire_id="london",
        variant="standard",
        position=position,
        owner_epoch="epoch",
        source_digest="digest",
        expected_uci="g1f3",
        policy_version="london-policy",
        version=0,
        introduced_at=NOW,
    )

    class Reader:
        def execute_native(self, sql, args=()):
            if "SELECT * FROM study_game_feedback" in sql:
                return Result([{}])
            if "FROM study_position_states" in sql:
                return Result(state)
            raise AssertionError(sql)

    fact = storage.read_position_urgency(
        Reader(), card_id="card", revision=1, repertoire_id="london"
    )
    assert fact.definition.key.position == position and fact.grade_effect_id is None
    state["introduced_at"] = NOW - timedelta(seconds=1)
    assert isinstance(
        storage.read_position_urgency(
            Reader(), card_id="card", revision=1, repertoire_id="london"
        ),
        Hold,
    )


def test_accepted_position_again_promotes_absent_card_then_checks_saved_settlement(
    encounter_owner,
):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))
    service.position_intake = NS(
        fence_position_intake=lambda *args: "position",
        position_introduced=lambda *args: True,
        apply_position_lapse=lambda key, *, day, now: ("position-receipt", now, 2),
    )
    qualification = qualification_for(command)
    service.accept_qualified(command, qualification=qualification)
    assert not store.queue
    calls = []

    class Targeting:
        def promote_effect(self, effect, day):
            calls.append(effect.key)
            store.queue[(day, effect.card_id)] = "urgent"
            return True

    service.targets = Targeting()
    store.promote_position_target = lambda effect, day, repertoire: (
        service.targets.promote_effect(effect, day)
    )
    service._saved_settlement = lambda effect: calls.append("settlement") or None
    receipt = service.refresh_encounter(command.key, qualification=qualification)
    assert receipt.admission_status == "available" and store.queue
    service.refresh_encounter(command.key, qualification=qualification)
    assert len(calls) == 2 and calls[1] == "settlement"


def test_retained_unqualified_feedback_fences_position_before_qualification(
    encounter_owner,
):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))
    service.accept_encounter(command)
    qualification = qualification_for(command)
    calls = []

    class Position:
        def position_introduced(self, command, qualification):
            return True

        def fence_position_intake(self, command, qualification):
            calls.append("fence")
            return "position"

        def apply_position_lapse(self, key, *, day, now):
            calls.append("grade")
            return ("position-receipt", now, 1)

    service.position_intake = Position()
    receipt = service.refresh_encounter(command.key, qualification=qualification)
    assert receipt.progress.position_grade_effect_id == "position-receipt"
    assert calls == ["fence", "grade"] and not store.reviews


def test_retained_qualification_with_staged_owner_holds_before_card_write(
    encounter_owner,
):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))
    service.accept_encounter(command)

    class Staged:
        def fence_position_intake(self, command, qualification):
            raise RuntimeError("position_owner_staged")

    service.position_intake = Staged()
    with pytest.raises(RuntimeError, match="position_owner_staged"):
        service.refresh_encounter(command.key, qualification=qualification_for(command))
    assert store.get(command.key).progress is None and not store.reviews


def test_missing_position_stays_pending_without_legacy_review(encounter_owner):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))
    service.position_intake = NS(
        fence_position_intake=lambda *args: "position",
        position_introduced=lambda *args: False,
        apply_position_lapse=lambda key, *, day, now: None,
    )
    receipt = service.accept_qualified(
        command, qualification=qualification_for(command)
    )
    assert receipt.reason == "position_introduction_pending" and not store.reviews


def test_unintroduced_carrier_of_introduced_position_gets_one_again(encounter_owner):
    from test_product_game_lapse import qualification_for

    store, _, _, _, service, command = encounter_owner
    assert store.facts.introduced_at is None
    calls = []

    class Position:
        def fence_position_intake(self, command, qualification):
            return "position"

        def position_introduced(self, command, qualification):
            # Another physical carrier has already introduced this shared key.
            return True

        def apply_position_lapse(self, key, *, day, now):
            calls.append(key)
            return ("shared-position-grade", now, 2)

    service.position_intake = Position()
    qualification = qualification_for(command)
    receipt = service.accept_qualified(command, qualification=qualification)
    assert receipt.disposition == "introduced_again"
    assert receipt.progress.position_grade_effect_id == "shared-position-grade"
    assert (
        service.accept_qualified(command, qualification=qualification).outcome
        == "duplicate"
    )
    assert (
        service.refresh_encounter(command.key, qualification=qualification).reason
        == "position_carrier_unintroduced"
    )
    assert len(calls) == 1 and not store.reviews and not store.queue


def test_position_disposition_reads_locked_exact_position(monkeypatch):
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey
    from tempo_legacy_database.adapters.study import (
        game_feedback_authority as authority,
        position_owner_epoch,
    )

    command, qualification, position = departure(LONDON_ORDER_A)
    definition = PositionDefinition(
        PositionKey("london", position), "g1f3", "london-policy"
    )
    source = NS(
        authored_token=NS(repertoire_id="london", authored_digest="digest"),
        routes=(NS(decisions=(NS(definition=definition),)),),
    )

    class DB:
        def execute_native(self, sql, args):
            assert "FOR UPDATE SKIP LOCKED" in sql and args == (
                "london",
                "standard",
                position,
            )
            return Result(
                dict(
                    expected_uci="g1f3",
                    policy_version="london-policy",
                    owner_epoch="epoch",
                    source_digest="digest",
                    introduced_at=NOW,
                )
            )

    monkeypatch.setattr(
        position_owner_epoch,
        "owner_mode",
        lambda *args: NS(disposition="position", epoch="epoch", reason=None),
    )
    decisions = object.__new__(authority.CurrentOwnerDecisions)
    decisions.database, decisions._position_source = DB(), source
    assert decisions.position_introduced(command, qualification) is True


def test_transposed_introduced_carrier_promotes_original_effect_and_settles(
    encounter_owner,
):
    from test_product_game_lapse import qualification_for
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )

    store, _, clock, _, service, command = encounter_owner
    qualification = qualification_for(command)
    selected = qualification.selection
    definition = PositionDefinition(
        PositionKey(selected.decision.repertoire_id, selected.position_key),
        selected.expected_uci[0],
        selected.surviving_routes[0].policy_id,
    )
    alias = selected.decision.model_copy(
        update={
            "presentation_id": "alias-card",
            "presentation_revision": 2,
            "decision_id": "different-alias-decision",
        }
    )
    carrier = (alias, {}, definition, "source-digest")

    class Position:
        def fence_position_intake(self, *args):
            return "position"

        def position_introduced(self, *args):
            return True

        def apply_position_lapse(self, key, *, day, now):
            return ("grade", now, 2)

        def fence_position_carrier(self, prepared):
            return prepared[0], prepared[2]

    service.position_intake = Position()
    service.accept_qualified(command, qualification=qualification)
    effect = store.get(command.key)
    assert (
        effect.disposition == "introduced_again"
        and not store.queue
        and not store.reviews
    )
    store.position_urgency = lambda *args: NS(
        definition=definition,
        grade_effect_id="grade",
        source_digest="source-digest",
        owner_epoch="epoch",
    )
    store.card_facts = lambda card, day: NS(
        revision=2,
        introduced_at=NOW - timedelta(days=1),
        available=True,
        active_attempt=False,
        buried=False,
    )
    store.promote_position_carrier = lambda effect, day: (
        store.queue.update({(day, "alias-card"): "urgent"}) or True
    )
    receipt = service.refresh_encounter(
        command.key, qualification=qualification, position_carrier=carrier
    )
    assert receipt.progress.target.card_id == "alias-card" and store.queue
    effect = store.get(command.key)
    assert (
        effect.key == command.key
        and effect.progress.position_grade_effect_id == "grade"
    )
    prior_fence = effect.progress.remediation_fence
    store.queue.clear()
    clock[0] = NOW + timedelta(days=1)
    service.refresh_encounter(
        command.key, qualification=qualification, position_carrier=carrier
    )
    effect = store.get(command.key)
    assert store.queue and effect.progress.remediation_fence == prior_fence
    issued = issued_attempt("alias-card", 2, 8, clock[0] + timedelta(minutes=1))
    effect = replace(
        effect,
        progress=effect.progress.model_copy(
            update={"first_issued": issued, "first_issued_ordinal": 1}
        ),
    )
    store.save(effect, NOW)
    store.card_effects = lambda card: (
        (store.get(command.key),) if card == "alias-card" else ()
    )
    store.position_completion_matches = lambda effect, completion: (
        completion.review_id == 42
    )
    adapter = object.__new__(PostgresGameFeedbackStore)
    adapter.position_completion_matches = store.position_completion_matches
    service.targets = adapter
    completion = Completion(
        42,
        "alias-card",
        2,
        clock[0] + timedelta(minutes=2),
        True,
        "position_study",
        issued,
    )
    service.settle(completion)
    assert store.get(command.key).status == "settled" and not store.reviews
    wrong = replace(completion, review_id=43)
    assert not adapter.completion_matches(effect, wrong)


def test_position_carrier_rejects_changed_certified_physical_binding(monkeypatch):
    from tempo_legacy_database.adapters.study import (
        game_feedback_authority as authority,
        current_feedback_authority,
    )

    decisions = object.__new__(authority.CurrentOwnerDecisions)
    decisions.database = object()
    decisions._position_source = NS(authored_token=NS(authored_digest="digest"))
    binding = NS(presentation_id="alias")
    prepared = (binding, {"revision": 2}, object(), "digest")
    monkeypatch.setattr(
        current_feedback_authority,
        "read_current_feedback_target",
        lambda *args: {"revision": 3},
    )
    with pytest.raises(
        authority.CurrentDecisionUnavailable, match="position_carrier_binding_changed"
    ):
        decisions.fence_position_carrier(prepared)
    with pytest.raises(
        authority.CurrentDecisionUnavailable, match="position_carrier_source_changed"
    ):
        decisions.fence_position_carrier(
            (binding, {"revision": 2}, object(), "old-digest")
        )


def test_position_carrier_queue_requires_exact_repertoire_scope(monkeypatch):
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )

    effect = NS(
        progress=NS(
            position_grade_effect_id="grade",
            target=NS(card_id="alias", card_revision=2),
            determined_at=NOW,
            stream="forward",
        ),
        key=NS(),
        target_authority={"kind": "position_carrier", "repertoire_id": "rep"},
    )

    class DB:
        def __init__(self, rows):
            self.rows, self.updated = rows, False

        def execute_native(self, sql, args=()):
            if "SELECT id,admission_repertoire_id" in sql:
                return Result(self.rows)
            if "UPDATE daily_queue" in sql:
                self.updated = True
                assert args == ("rep", NOW.date().isoformat(), "alias")
                return Result([{"id": 1}])
            raise AssertionError(sql)

    # Avoid constructing a Games key; the promotion passes through its stable identity.
    from tempo_legacy_database.adapters.study import game_feedback as storage

    monkeypatch.setattr(storage, "effect_id", lambda key: "effect")
    db = DB([])
    adapter = object.__new__(PostgresGameFeedbackStore)
    adapter.database = db
    adapter.promote_encounter = lambda *args, **kwargs: None
    assert (
        adapter.promote_position_carrier(effect, NOW.date().isoformat()) and db.updated
    )
    db.rows = [{"id": 1, "admission_repertoire_id": "other"}]
    db.updated = False
    assert (
        not adapter.promote_position_carrier(effect, NOW.date().isoformat())
        and not db.updated
    )
    db.rows = [{"id": 1, "admission_repertoire_id": None}]
    assert not adapter.promote_position_carrier(effect, NOW.date().isoformat())


def test_position_carrier_settlement_requires_original_durable_game_grade(monkeypatch):
    key = NS(repertoire_id="rep", variant="standard", position="fen")
    definition = NS(key=key, expected_uci="e2e4", policy_version="policy")
    witness = NS(definition=definition, position_revision=4, owner_epoch="epoch")
    plan = NS(attempt_id="a", due_positions=(witness,))
    response = NS(
        definition=definition,
        assistance_before=False,
        manual_failure=False,
        correct=True,
        event_id="event",
    )
    monkeypatch.setattr(grades, "_load_plan", lambda *args: plan)
    monkeypatch.setattr(grades, "_completion_saved", lambda *args: True)
    monkeypatch.setattr(
        grades, "_load_events", lambda *args: ((response, "expected", "digest", NOW),)
    )
    authority = dict(
        kind="position_carrier",
        grade_effect_id="original-grade",
        owner_epoch="epoch",
        source_digest="digest",
        expected_uci="e2e4",
        policy_version="policy",
        repertoire_id="rep",
        variant="standard",
        position="fen",
    )
    effect = NS(
        qualification=None,
        lapse=NS(
            finding=NS(repertoire_id="rep", position_key="fen", expected_uci=("e2e4",))
        ),
        key=NS(logical_id=lambda: "game-key"),
        target_authority=authority,
        progress=NS(position_grade_effect_id="original-grade"),
    )
    completion = NS(review_id=1, card_id="alias", revision=1)
    assert not grades.position_completion_matches(Database(None), effect, completion)
    wrong = dict(
        effect_id="different-grade",
        version_after=4,
        repertoire_id="rep",
        variant="standard",
        position="fen",
        owner_epoch="epoch",
        source_digest="digest",
        expected_uci="e2e4",
        policy_version="policy",
    )
    assert not grades.position_completion_matches(Database(wrong), effect, completion)
    wrong["effect_id"] = "original-grade"
    assert grades.position_completion_matches(Database(wrong), effect, completion)


def test_position_carrier_recovery_prepares_the_retained_alias(monkeypatch):
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey
    from tempo_legacy_database.adapters.study import (
        game_feedback_authority as authority,
        current_feedback_authority,
    )

    _, qualification, position = departure(LONDON_ORDER_A)
    decision = qualification.selection.decision
    definition = PositionDefinition(
        PositionKey("london", position), "g1f3", "london-policy"
    )
    earlier = decision.model_copy(
        update={
            "presentation_id": "a-card",
            "presentation_revision": 1,
            "introduction": "introduced",
            "available": True,
        }
    )
    retained = decision.model_copy(
        update={
            "presentation_id": "z-card",
            "presentation_revision": 2,
            "introduction": "introduced",
            "available": True,
        }
    )
    source = NS(
        authored_token=NS(authored_digest="digest"),
        snapshot=NS(decision_presentations=(earlier, retained)),
        routes=(NS(decisions=(NS(definition=definition),)),),
    )

    def facts(db, binding):
        return {
            "card": dict(
                revision=binding.presentation_revision,
                introduced_at=NOW,
                content_type="opening",
                archived=False,
                pending_validation=False,
                superseded_by=None,
            ),
            "memberships": [{"repertoire_id": "london"}],
            "graphs": [{"line_id": binding.route_id}],
        }

    monkeypatch.setattr(
        current_feedback_authority, "read_current_feedback_target", facts
    )
    monkeypatch.setattr(
        authority.CurrentOwnerDecisions,
        "_carrier_executable",
        staticmethod(lambda *args: True),
    )
    decisions = object.__new__(authority.CurrentOwnerDecisions)
    decisions._position_source = source
    target = NS(card_id="z-card", card_revision=2)
    assert (
        decisions.prepare_position_carrier(
            object(), qualification, retained_target=target
        )[0]
        == retained
    )
