"""Real Learning PostgreSQL, installed module, explicit caller transactions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import psycopg
import pytest
from tempo_game_evidence_contracts import v2
from tempo_learning_remediation import (AdmissionResult, AttemptAuthority, Candidate, PostgresRepository,
    RemediationError, ReviewOutcome, apply_admission, prepare_admissions)

spec = importlib.util.spec_from_file_location("remediation_fixtures", Path(__file__).parents[1] / "tests/support.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
DECISION, NOW, delivery, context, withdrawal = support.DECISION, support.NOW, support.delivery, support.context, support.withdrawal


@pytest.fixture
def workspace():
    global DECISION
    assert os.environ.get("TEMPO_REMEDIATION_OWNED", "").startswith("tempo264-")
    identity = "remediation-" + uuid4().hex
    DECISION = replace(support.DECISION, workspace_id=identity)
    return identity


def connect():
    return psycopg.connect(os.environ["TEMPO_REMEDIATION_DATABASE"], autocommit=True)


def scoped(value, workspace, **changes):
    return value.model_copy(update={"workspace_id": workspace, **changes})


def accept(repository, value, **changes):
    changes.setdefault("accepted", DECISION)
    return repository.receive(value, replace(context(value, **changes), workspace_id=repository.workspace_id))


def attempt(identifier="attempt", **changes):
    return replace(AttemptAuthority(identifier, "card", 1, "publication", 1, NOW + timedelta(seconds=1)), **changes)


def review(bound, identifier="review", **changes):
    return replace(ReviewOutcome(identifier, bound, NOW + timedelta(seconds=2), "correct"), **changes)


def count(connection, table, workspace):
    return connection.execute("SELECT count(*) FROM " + table + " WHERE workspace_id=%s", (workspace,)).fetchone()[0]


def test_native_outer_rollback_includes_obligation_and_completed_receipt(workspace):
    with connect() as connection:
        with pytest.raises(RuntimeError, match="outer transaction"):
            PostgresRepository(connection, workspace)
        with pytest.raises(RuntimeError, match="injected"):
            with connection.transaction():
                repository = PostgresRepository(connection, workspace)
                assert accept(repository, scoped(delivery(), workspace)).status == "completed"
                assert repository.receipt("delivery").status == "completed"
                raise RuntimeError("injected outer failure")
        assert count(connection, "remediation_deliveries", workspace) == 0
        assert count(connection, "remediation_contributions", workspace) == 0


def test_native_response_loss_restart_and_new_transport_have_one_effect(workspace):
    value = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        first = accept(PostgresRepository(connection, workspace), value)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        assert accept(repository, value) == first
        retry = accept(repository, value.model_copy(update={"delivery_id": "retry"}))
        assert retry.learning_revision == first.learning_revision
        assert count(connection, "remediation_incident_versions", workspace) == 1
        assert count(connection, "remediation_contributions", workspace) == 1
        assert count(connection, "remediation_deliveries", workspace) == 2


def test_native_pending_failed_and_completed_are_durable_acceptance_states(workspace):
    value = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        assert repository.record_status(value, "accepted_pending", "awaiting_acceptance").learning_revision is None
        assert not repository.has_outstanding("card")
        assert repository.record_status(value, "retryable_failure", "temporary_failure").accepted_obligation_ids == []
        assert repository.receipt("delivery").status == "retryable_failure"
        assert accept(repository, value).status == "completed"
        assert repository.record_status(value, "accepted_pending", "retry").status == "completed"
        assert repository.pending_page().items[0].state == "pending"


@pytest.mark.parametrize("identity", ["delivery", "evidence", "obligation"])
def test_native_immutable_identity_conflict_has_no_partial_effect(workspace, identity):
    value = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, value)
        data = value.model_dump(mode="json")
        if identity == "delivery": data["game_id"] = "different"
        if identity == "evidence":
            data["delivery_id"] = "new"
            data["encounters"][0]["observed_move"] = "b2b3"
        if identity == "obligation":
            data.update(delivery_id="new", evidence_revision=2)
            data["obligations"][0]["source_revision"] = 2
        assert accept(repository, v2.EvidenceDelivery.model_validate(data)).status == "conflict"
        assert count(connection, "remediation_deliveries", workspace) == 1
        assert count(connection, "remediation_contributions", workspace) == 1


@pytest.mark.parametrize("kind", ["publication", "alternative", "game_revision", "foreign_workspace", "forged_card"])
def test_native_stale_or_forged_supported_miss_is_not_accepted(workspace, kind):
    value = scoped(delivery(), workspace)
    if kind == "alternative":
        data = value.model_dump(mode="json")
        data["encounters"][0]["observed_move"] = "c2c4"
        value = v2.EvidenceDelivery.model_validate(data)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        expected = replace(context(value, accepted=replace(DECISION, active=False) if kind == "publication" else DECISION), workspace_id=workspace)
        if kind == "foreign_workspace": expected = replace(expected, content=support.Authority(replace(DECISION, workspace_id="foreign")))
        if kind == "forged_card": expected = replace(expected, content=support.Authority(replace(DECISION, card_id="unrelated", aliases=())))
        if kind == "game_revision": expected = replace(expected, game_revision=2)
        assert repository.receive(value, expected).status in {"terminal_failure", "stale_revision"}
        assert count(connection, "remediation_deliveries", workspace) == 0
        assert not repository.has_outstanding("card")


def test_native_aliases_reanalysis_and_distinct_games_keep_logical_identity(workspace):
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        for value in (delivery(), delivery(evidence="alias-source", obligation="alias", card="route-alias", transport="alias")):
            assert accept(repository, scoped(value, workspace)).status == "completed"
        assert len(repository.pending_page().items) == 1
        bound = attempt()
        assert repository.bind_attempt(bound, DECISION) == 1
        assert repository.settle(review(bound), DECISION)
        assert not repository.has_outstanding("card")
        assert accept(repository, scoped(delivery(evidence="reanalyzed", obligation="new-id", transport="reanalysis"), workspace)).status == "completed"
        assert not repository.has_outstanding("card")
        assert count(connection, "remediation_incident_versions", workspace) == 1
        assert accept(repository, scoped(delivery(game="later-game", evidence="later", obligation="later", transport="later"), workspace)).status == "completed"
        assert repository.bind_attempt(bound, DECISION) == 1
        assert repository.settle(review(bound), DECISION)
        assert repository.has_outstanding("card")


def test_native_withdrawal_is_source_specific_and_partial_omission_is_additive(workspace):
    first = scoped(delivery(), workspace)
    second = scoped(delivery(game="other", evidence="other", obligation="other", transport="other"), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, first); accept(repository, second)
        partial = first.model_dump(mode="json")
        partial.update(delivery_id="partial", evidence_revision=2, obligations=[])
        assert accept(repository, v2.EvidenceDelivery.model_validate(partial)).status == "completed"
        assert accept(repository, withdrawal(first, revision=3)).status == "completed"
        assert repository.has_outstanding("card")
        assert accept(repository, withdrawal(second)).status == "completed"
        assert not repository.has_outstanding("card")
        assert count(connection, "remediation_contributions", workspace) == 2


def test_native_equivalent_publication_reanalysis_keeps_satisfied_incident_without_authorizing_stale_attempt(workspace):
    first = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, first)
        bound = attempt(); repository.bind_attempt(bound, DECISION)
        assert repository.settle(review(bound), DECISION)
        data = scoped(delivery(revision=2, transport="reanalysis"), workspace).model_dump(mode="json")
        data["obligations"][0]["publication_generation"] = 2
        data["comparisons"][0]["publication_generation"] = 2
        current = replace(DECISION, publication_generation=2)
        assert accept(repository, v2.EvidenceDelivery.model_validate(data), accepted=current).status == "completed"
        assert count(connection, "remediation_incident_versions", workspace) == 1
        assert not repository.has_outstanding("card")
        with pytest.raises(RemediationError, match="authority_changed"):
            repository.settle(review(bound), current)
        assert not repository.has_outstanding("card")


def test_native_old_withdrawal_or_review_cannot_clear_corrected_revision(workspace):
    first = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, first)
        bound = attempt(); repository.bind_attempt(bound, DECISION)
        data = scoped(delivery(revision=2, transport="correction"), workspace).model_dump(mode="json")
        data["encounters"][0]["observed_move"] = "b2b3"
        corrected = v2.EvidenceDelivery.model_validate(data)
        assert accept(repository, corrected).status == "completed"
        assert accept(repository, withdrawal(first, revision=3)).status == "stale_revision"
        assert not repository.settle(review(bound), DECISION)
        assert repository.has_outstanding("card")
        assert accept(repository, withdrawal(corrected, revision=3, obligation_revision=2)).status == "completed"
        assert not repository.has_outstanding("card")


@pytest.mark.parametrize("unknown", [False, True])
def test_native_late_import_and_unknown_time_require_a_fresh_bound_attempt(workspace, unknown):
    value = scoped(delivery(occurred_at=None if unknown else "2026-10-13T12:00:00Z"), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        old = attempt("old", issued_at=NOW-timedelta(days=1))
        repository.bind_attempt(old, DECISION)
        old_review = review(old, "old-review", reviewed_at=NOW-timedelta(hours=1))
        assert not repository.settle(old_review, DECISION)
        accept(repository, value)
        assert not repository.settle(old_review, DECISION)
        assert repository.has_outstanding("card")
        fresh = attempt("fresh"); repository.bind_attempt(fresh, DECISION)
        assert repository.settle(review(fresh, "fresh-review"), DECISION)
        assert not repository.has_outstanding("card")


@pytest.mark.parametrize("changes", [dict(outcome="again"), dict(guided=True), dict(failed=True),
    dict(invalidated=True), dict(source_kind="game"), dict(reviewed_at=NOW-timedelta(days=2))])
def test_native_only_unaided_correct_valid_study_settles(workspace, changes):
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace))
        bound = attempt(); repository.bind_attempt(bound, DECISION)
        assert not repository.settle(review(bound, **changes), DECISION)
        assert repository.has_outstanding("card")
        assert count(connection, "remediation_settlements", workspace) == 0


def test_native_attempt_frontier_cannot_be_invented_changed_or_expanded(workspace):
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace))
        bound = attempt()
        with pytest.raises(RemediationError, match="frontier_missing"):
            repository.settle(review(bound), DECISION)
        repository.bind_attempt(bound, DECISION)
        with pytest.raises(RemediationError, match="attempt_id_reused"):
            repository.bind_attempt(replace(bound, issued_at=NOW+timedelta(seconds=3)), DECISION)
        with pytest.raises(RemediationError, match="authority_changed"):
            repository.settle(review(bound), replace(DECISION, card_revision=2))
        assert repository.has_outstanding("card")


def test_native_invalidation_restores_work_without_editing_schedule_or_history(workspace):
    with connect() as connection, connection.transaction():
        connection.execute("INSERT INTO remediation_test_schedule VALUES(%s,'card','same-fsrs','genuine-history')", (workspace,))
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace))
        bound = attempt(); repository.bind_attempt(bound, DECISION); repository.settle(review(bound), DECISION)
        assert not repository.has_outstanding("card")
        repository.invalidate_settlement("review", "controlled_review_invalidated")
        assert repository.has_outstanding("card")
        assert connection.execute("SELECT fsrs_json,history_json FROM remediation_test_schedule WHERE workspace_id=%s", (workspace,)).fetchone() == ("same-fsrs", "genuine-history")


def test_native_rejected_pages_advance_with_a_four_page_bound(workspace):
    class Facts:
        def __init__(self): self.sizes = []
        def candidates(self, items):
            self.sizes.append(len(items))
            return ()
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        for index in range(70):
            card = f"card-{index:03}"
            accept(repository, scoped(delivery(card=card, game=card, evidence=card, obligation=card, transport=card), workspace), accepted=replace(DECISION, card_id=card))
        facts = Facts(); prepared = prepare_admissions(repository, facts)
        assert prepared.examined_pages == 4 and prepared.has_more and prepared.cursor == "card-063"
        assert facts.sizes == [16]*4 and prepared.candidates == ()
        last = prepare_admissions(repository, facts, prepared.cursor)
        assert not last.has_more and last.cursor == "card-069" and last.examined_pages == 1


@pytest.mark.parametrize("introduced", [False, True])
def test_native_zero_limit_paths_keep_deferral_head_schedule_and_history(workspace, introduced):
    class Writer:
        def __init__(self): self.calls = []
        def apply(self, proposal):
            self.calls.append(proposal)
            return AdmissionResult("deferred", "foreground_capacity_busy")
    with connect() as connection, connection.transaction():
        connection.execute("INSERT INTO remediation_test_schedule VALUES(%s,'card','same-fsrs','same-history')", (workspace,))
        connection.execute("INSERT INTO remediation_test_queue VALUES(%s,'issued-head',0,true)", (workspace,))
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace))
        candidate = Candidate("card", 1, "publication", 1, "route", introduced, True, progression_allowed=False, remaining_allowance=0)
        writer = Writer(); result = apply_admission(repository, candidate, writer)
        assert result.reason == "foreground_capacity_busy" and len(writer.calls) == 1
        assert writer.calls[0].path == ("review" if introduced else "introduction")
        item = repository.pending_page().items[0]
        assert item.state == "deferred" and item.reason == result.reason
        assert repository.receipt("delivery").status == "completed"
        assert connection.execute("SELECT fsrs_json,history_json FROM remediation_test_schedule WHERE workspace_id=%s", (workspace,)).fetchone() == ("same-fsrs", "same-history")
        assert connection.execute("SELECT card_id,position,protected FROM remediation_test_queue WHERE workspace_id=%s", (workspace,)).fetchall() == [("issued-head", 0, True)]


def test_native_admission_and_module_state_share_outer_rollback(workspace):
    with connect() as connection:
        with connection.transaction(): accept(PostgresRepository(connection, workspace), scoped(delivery(), workspace))
        class Writer:
            def apply(self, proposal):
                connection.execute("INSERT INTO remediation_test_queue VALUES(%s,%s,1,false)", (workspace, proposal.card_id))
                return AdmissionResult("applied", queue_entry_id=1)
        with pytest.raises(RuntimeError, match="outer failure"):
            with connection.transaction():
                repository = PostgresRepository(connection, workspace)
                assert apply_admission(repository, Candidate("card", 1, "publication", 1, "route", False, True), Writer()).status == "applied"
                raise RuntimeError("outer failure")
        with connection.transaction():
            assert PostgresRepository(connection, workspace).pending_page().items[0].state == "pending"
            assert count(connection, "remediation_test_queue", workspace) == 0


@pytest.mark.parametrize("change", [dict(card_revision=2), dict(publication_id="replacement"), dict(publication_generation=2)])
def test_native_stale_content_obligation_remains_visible_without_admission_authority(workspace, change):
    class Writer:
        def apply(self, proposal): raise AssertionError("stale obligation reached admission writer")
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace))
        candidate = replace(Candidate("card", 1, "publication", 1, "route", False, True), **change)
        assert apply_admission(repository, candidate, Writer()).reason == "obligation_not_current"
        assert repository.has_outstanding("card")
        assert repository.pending_page().items[0].reason == "obligation_not_current"


def test_native_game_revision_change_defers_old_support_without_implicit_withdrawal(workspace):
    class Writer:
        def apply(self, proposal): raise AssertionError("obsolete game fact reached writer")
    first = scoped(delivery(), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace); accept(repository, first)
        newer = first.model_dump(mode="json")
        newer.update(delivery_id="new-revision", evidence_revision=2, game_revision=2, obligations=[])
        assert accept(repository, v2.EvidenceDelivery.model_validate(newer)).status == "completed"
        candidate = Candidate("card", 1, "publication", 1, "route", False, True)
        assert repository.has_outstanding("card")
        assert apply_admission(repository, candidate, Writer()).reason == "obligation_not_current"
        assert count(connection, "remediation_contributions", workspace) == 1
        bound = attempt(); repository.bind_attempt(bound, DECISION)
        assert not repository.settle(review(bound), DECISION)
        assert repository.has_outstanding("card")


@pytest.mark.parametrize("change,reason", [(dict(protected=True), "issued_head_preserved"), (dict(buried=True), "buried_today")])
def test_native_issued_head_and_burial_never_call_admission_writer(workspace, change, reason):
    class Writer:
        def apply(self, proposal): raise AssertionError("protected work reached writer")
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace); accept(repository, scoped(delivery(), workspace))
        candidate = replace(Candidate("card", 1, "publication", 1, "route", True, True), **change)
        assert apply_admission(repository, candidate, Writer()).reason == reason
        assert repository.has_outstanding("card")


def test_native_unknown_time_cannot_settle_with_retrospectively_bound_old_issuance(workspace):
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(occurred_at=None), workspace))
        old = attempt(issued_at=NOW-timedelta(seconds=1))
        assert repository.bind_attempt(old, DECISION) == 1
        assert not repository.settle(review(old), DECISION)
        assert repository.has_outstanding("card")


def wait_for_lock(connection, pid):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        connection.execute("SELECT pg_stat_clear_snapshot()")
        row = connection.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
        if row and row[0] == "Lock": return
        time.sleep(.01)
    raise AssertionError("competitor did not reach a real PostgreSQL lock")


def test_native_concurrent_duplicate_delivery_is_exactly_once(workspace):
    value = scoped(delivery(), workspace); ready = Event(); pids = []
    with connect() as first:
        def competitor():
            with connect() as second, second.transaction():
                pids.append(second.info.backend_pid); ready.set()
                return accept(PostgresRepository(second, workspace), value.model_copy(update={"delivery_id": "retry"}))
        with ThreadPoolExecutor(max_workers=1) as executor:
            with first.transaction():
                receipt = accept(PostgresRepository(first, workspace), value)
                future = executor.submit(competitor); assert ready.wait(5)
                wait_for_lock(first, pids[0])
            assert future.result(timeout=10).learning_revision == receipt.learning_revision
        assert count(first, "remediation_incident_versions", workspace) == 1
        assert count(first, "remediation_contributions", workspace) == 1


def test_native_background_intake_yields_instead_of_waiting_on_foreground(workspace):
    with connect() as first, first.transaction():
        PostgresRepository(first, workspace).lock()
        with connect() as second, second.transaction():
            repository = PostgresRepository(second, workspace, background=True)
            response = accept(repository, scoped(delivery(), workspace))
            assert response.status == "retryable_failure" and response.reason == "foreground_busy"
            assert repository.receipt("delivery") is None
            assert not repository.has_outstanding("card")


@pytest.mark.parametrize("miss_first", [False, True])
def test_native_new_miss_review_completion_race_preserves_newer_work(workspace, miss_first):
    bound = attempt()
    new_value = scoped(delivery(game="later", evidence="later", obligation="later", transport="later"), workspace)
    with connect() as connection, connection.transaction():
        repository = PostgresRepository(connection, workspace)
        accept(repository, scoped(delivery(), workspace)); repository.bind_attempt(bound, DECISION)
    ready = Event(); pids = []
    def operate(repository, is_miss):
        return accept(repository, new_value) if is_miss else repository.settle(review(bound), DECISION)
    with connect() as first:
        def competitor():
            with connect() as second, second.transaction():
                pids.append(second.info.backend_pid); ready.set()
                return operate(PostgresRepository(second, workspace), not miss_first)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with first.transaction():
                operate(PostgresRepository(first, workspace), miss_first)
                future = executor.submit(competitor); assert ready.wait(5)
                wait_for_lock(first, pids[0])
            future.result(timeout=10)
        with first.transaction():
            repository = PostgresRepository(first, workspace)
            assert repository.has_outstanding("card")
            assert repository.bind_attempt(bound, DECISION) == 1
            assert repository.settle(review(bound), DECISION)
            assert repository.has_outstanding("card")
