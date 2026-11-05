"""Current qualified feedback proves exact authority without legacy capture."""

from copy import deepcopy

import pytest

from app.product_composition import provider_priority
from tempo_legacy_database.adapters.study.current_feedback_authority import (
    CurrentQualifiedOwnerDecisions,
)
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    CurrentDecisionUnavailable,
)
from test_product_delivery_qualification import (
    native_handoff,
    owned_postgres,
    setup_case,
    qualify,
    command,
)
from test_game_bound_repertoire_preparation import authored, candidate
from test_product_snapshot import repertoire_facts
from app.repertoire import PreparedFirstBoundaryRepertoire


def prepared_case():
    case = setup_case()
    facts = repertoire_facts()
    publication = PreparedFirstBoundaryRepertoire(
        authored(facts), game=case.game
    ).with_bindings(candidate(facts))
    case.snapshot = publication.snapshot
    case.command = command(case.game, case.snapshot, 3)
    case.source = case.source.model_copy(update={"effects": (case.command,)})
    delivery = qualify(case)
    actual = next(
        row
        for row in facts["cards"]
        if row["id"] == delivery.qualification.selection.decision.presentation_id
    )
    return case, publication, delivery, dict(card=actual, header=facts["repertoire"])


def test_current_qualified_constructor_requires_no_legacy_export_and_reuses_complete_scope():
    from app.games.feedback.first_boundary import select_first_boundary

    case, publication, delivery, target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game, target_facts=target)

    def selector(game, **arguments):
        return select_first_boundary(
            game, **arguments, prepared_repertoires=(publication,)
        )

    decisions.prepare_qualified(
        case.command,
        delivery.qualification,
        qualified_publications=(publication,),
        qualification_fence=lambda *args: None,
        game=case.game,
        select_first_boundary=selector,
    )
    assert not hasattr(decisions, "authority") and not hasattr(decisions, "proof")
    assert (
        decisions.introduction_revision(case.command.decision.repertoire_id)
        == target["header"]["scope_source_revision"] + 1
    )
    assert decisions._qualified_captures == (
        publication.authored_facts()["repertoire"],
    )
    assert decisions._prepared_encounter is not None


@pytest.mark.parametrize("fault", ["source", "occurrence", "interior_definition"])
def test_current_qualified_authority_rejects_changed_source_or_exact_executable(fault):
    case, publication, delivery, target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game, target_facts=target)
    if fault == "source":
        changed = case.game.model_copy(update={"source_digest": "b" * 64})
        with pytest.raises(
            CurrentDecisionUnavailable, match="canonical_source_unproved"
        ):
            decisions._validate_qualified_game(changed, case.command)
    elif fault == "occurrence":
        changed = case.command.model_copy(
            update={"occurred_at": "2020-01-01T00:00:00Z"}
        )
        with pytest.raises(
            CurrentDecisionUnavailable, match="canonical_source_unproved"
        ):
            decisions._validate_qualified_game(case.game, changed)
    else:
        target = deepcopy(target)
        target["card"]["moves_json"] = '["d2d4"]'
        decisions = CurrentQualifiedOwnerDecisions(game=case.game, target_facts=target)
        with pytest.raises(
            CurrentDecisionUnavailable, match="decision_not_in_executable"
        ):
            decisions.prepare_encounter(case.command)


def test_native_current_feedback_accepts_with_broad_legacy_capture_unavailable(
    native_handoff, monkeypatch
):
    from app import legacy_game_observation
    from app.services import legacy_game_capture
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )

    case = native_handoff
    original = CurrentQualifiedOwnerDecisions._mapping_for_target
    observed = []

    def observe_mapping(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(
        CurrentQualifiedOwnerDecisions, "_mapping_for_target", observe_mapping
    )

    def forbidden(*args, **kwargs):
        pytest.fail(
            "current qualified feedback invoked broad legacy capture/projection"
        )

    monkeypatch.setattr(legacy_game_observation, "capture_game", forbidden)
    monkeypatch.setattr(legacy_game_capture, "capture_source_scope", forbidden)
    monkeypatch.setattr(provider_priority, "project_capture", forbidden)
    monkeypatch.setattr(provider_priority, "_owner_capture", forbidden)
    prepared = provider_priority.prepare_delivery_feedback(
        case.original,
        connections=case.connections,
        clock=case.clock,
        admitted=lambda: True,
        capture=forbidden,
    )
    assert prepared.provider_feedback is not None, (
        prepared.delivery.qualification_reason
    )
    assert (
        prepared.provider_feedback.target_capture is None
        and prepared.provider_feedback.target_snapshot is None
    )
    with case.connections.writer() as database:
        result = provider_priority.apply_prepared_determined_feedback(
            database,
            prepared,
            clock=case.clock,
            current_runtime_id=prepared.mode_runtime_id,
        )
        assert result.progressed, result.held_reason
        assert observed, "exact target mapping fence was not exercised"
        effect = PostgresGameFeedbackStore(database).get(case.original.command.key)
        assert effect is not None and effect.key == case.original.command.key
    assert case.store.delivery(case.original.operation_id).state == "acknowledged"


@pytest.mark.parametrize(
    "fault", ["ambiguous", "missing_graph_route", "wrong_ply", "revision"]
)
def test_exact_target_requires_complete_equivalent_route_and_revision(fault):
    case, publication, delivery, target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game, target_facts=target)
    decisions._qualified_snapshots = (publication.snapshot,)
    selected = delivery.qualification.selection.decision
    current = case.command.model_copy(update={"decision": selected})
    mappings = [
        item
        for item in publication.snapshot.decision_presentations
        if item.decision_id == current.key.decision_id
    ]
    assert mappings
    actual = dict(target["card"])
    graphs = [{"line_id": item.route_id} for item in mappings]
    assert decisions._mapping_for_target(current, actual, graphs) == selected
    if fault == "ambiguous":
        altered = mappings[0].model_copy(update={"presentation_id": "other-card"})
        decisions._qualified_snapshots = (
            publication.snapshot.model_copy(
                update={
                    "decision_presentations": (
                        *publication.snapshot.decision_presentations,
                        altered,
                    )
                }
            ),
        )
        expected = "conflicting_decision_bindings"
    elif fault == "missing_graph_route":
        graphs.append({"line_id": "unrepresented-route"})
        expected = "incomplete_decision_bindings"
    elif fault == "wrong_ply":
        current = current.model_copy(
            update={
                "decision": selected.model_copy(
                    update={"route_ply": selected.route_ply + 1}
                )
            }
        )
        expected = "supplied_route_unproved"
    else:
        actual["revision"] += 1
        expected = "card_revision_changed"
    with pytest.raises(CurrentDecisionUnavailable, match=expected):
        decisions._mapping_for_target(current, actual, graphs)


@pytest.mark.parametrize("change", ["target_revision", "integrity_card_block"])
def test_native_current_qualified_target_changes_hold_original_delivery(
    native_handoff, monkeypatch, change
):
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )

    case = native_handoff
    prepared = provider_priority.prepare_determined_feedback(
        workspace=case.workspace,
        connections=case.connections,
        clock=case.clock,
        admitted=lambda: True,
        capture=case.capture,
    )
    assert prepared.provider_feedback is not None
    target = prepared.provider_feedback.qualification.selection.decision
    reached = []
    original = CurrentQualifiedOwnerDecisions._current_prepared_qualified

    def observe(self, *args, **kwargs):
        reached.append("entered")
        try:
            return original(self, *args, **kwargs)
        except CurrentDecisionUnavailable as error:
            reached.append(error.reason)
            raise

    monkeypatch.setattr(
        CurrentQualifiedOwnerDecisions, "_current_prepared_qualified", observe
    )
    with case.case.transaction() as database:
        if change == "target_revision":
            database.execute_native(
                "UPDATE cards SET revision=revision+1 WHERE id=%s",
                (target.presentation_id,),
            )
        else:
            issue_id = "target-block-" + case.original.operation_id
            database.execute_native(
                """INSERT INTO repertoire_integrity_issues
                (id,repertoire_id,kind,signature,created_at,updated_at)
                VALUES(%s,%s,'invalid_source','target-block','2026-01-01','2026-01-01')""",
                (issue_id, target.repertoire_id),
            )
            database.execute_native(
                """INSERT INTO repertoire_integrity_card_blocks
                (repertoire_id,card_id,issue_id,scan_generation,published_at)
                VALUES(%s,%s,%s,'target-block','2026-01-01')""",
                (target.repertoire_id, target.presentation_id, issue_id),
            )
    before = case.case.readback()
    with case.case.transaction() as database:
        result = provider_priority.apply_prepared_determined_feedback(
            database, prepared, clock=case.clock, current_runtime_id=case.runtime_id
        )
        assert not result.progressed and result.pending
        assert (
            PostgresGameFeedbackStore(database).get(case.original.command.key) is None
        )
    assert case.case.readback() == before
    actual = case.store.delivery(case.original.operation_id)
    assert actual.command == case.original.command and actual.state == "pending"
    assert actual.operation_id == case.original.operation_id and actual.receipt is None
    assert actual.attempts == 0
    print("TARGET_FENCE_OBSERVATION", change, reached, result.held_reason)


def test_current_target_read_without_integrity_service_import():
    """The real selected read validates certificates without loading the worker service."""
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent("""
        import importlib.abc
        import json
        import sys
        from types import SimpleNamespace

        name = 'app.services.postgres_integrity'
        assert name not in sys.modules
        class Poison(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == name:
                    raise ImportError('forbidden integrity service')
        sys.meta_path.insert(0, Poison())
        from tempo_legacy_database.adapters.study.current_feedback_authority import read_current_feedback_target
        from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable
        assert name not in sys.modules
        header = dict(scope_source_revision=1, canonical_prefix_revision=1,
            graph_state='ready', graph_generation=7, integrity_status='clean',
            scan_status='idle', scan_generation='scan:1', checked_at='2026-10-16T12:00:00Z',
            scan_completed_sources=3, scan_total_sources=3, scan_error=None,
            graph_task_generation=7, graph_task_state='complete', scan_task_id='scan',
            scan_task_generation=1, scan_task_state='complete',
            payload_json=json.dumps(dict(repertoire_id='ruy', graph_generation=7)))
        class Database:
            def __init__(self, row): self.results = iter([row, {'id':'card'}, [], []])
            def execute_native(self, sql, parameters):
                result = next(self.results)
                return SimpleNamespace(fetchone=lambda: result, fetchall=lambda: result)
        target = SimpleNamespace(repertoire_id='ruy', presentation_id='card')
        for status in ('clean', 'needs_repair'):
            result = read_current_feedback_target(Database(header | {'integrity_status': status}), target)
            assert result['certificate']['integrity_status'] == status
            assert 'payload_json' not in result['certificate']
        for changes in ({'payload_json':'{'}, {'graph_task_generation':8}, {'scan_task_state':'queued'}):
            try:
                read_current_feedback_target(Database(header | changes), target)
            except CurrentDecisionUnavailable as exc:
                assert 'current_owner_integrity_unavailable' in str(exc)
            else:
                raise AssertionError('unsafe certificate accepted')
        assert name not in sys.modules
    """)
    subprocess.run([sys.executable, "-c", probe], check=True)
