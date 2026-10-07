"""Issue 79: exact read-only transition consequences and fail-closed fencing."""
from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from app.services.prefix_evaluation import evaluate_prefix, PrefixEvaluationError
from app.services.prefix_transition import (
    SnapshotTable, TransitionSnapshot, canonical_json, plan_transition,
    transition_snapshot_identity, validate_plan_freshness,
)
from test_prefix_evaluation import line, snapshot, split, CARO_A, CARO_B, QGD


def card_row(step, **changes):
    return dict(id=step.card_id, repertoire_id='rep', start_fen=step.starting_fen,
                moves_json=json.dumps(step.moves), trained_color=step.trained_color,
                revision=1, archived=0, superseded_by=None, pending_validation=0,
                content_type='opening', canonical_route_source=0,
                kind='prefix' if step.segment_kind == 'prefix' else 'response',
                state='learning', introduced_at='2026-10-01', due_date='2026-10-08',
                interval_days=3, fsrs_card_json=None, first_correct_at=None,
                reinforcement_pending=0, stability=2, guided_review=0,
                scheduling_mode='normal', hard_correct_streak=0, recent_attempts_json='[]') | changes


def prepared_snapshot(source=None, **tables):
    source = source or snapshot((line('caro'), line('qgd', QGD)))
    cards = list({step.card_id: card_row(step) for step in source.published_steps}.values())
    links = [dict(repertoire_id='rep', card_id=card['id'], canonical_route_source=0) for card in cards]
    inputs = {'cards': cards, 'repertoire_cards': links, **tables}
    lookup_ids = {row['id'] for row in inputs['cards']} | {row['card_id'] for row in inputs['repertoire_cards']}
    # Capture lookups for all graph targets, including their absence.
    for selected_depth in range(1, 4):
        proposed = evaluate_prefix(source, [item.id for item in source.lines], {item.id: selected_depth for item in source.lines})
        lookup_ids.update(card['card_id'] for card in proposed['whole_repertoire']['proposed']['cards'])
    return TransitionSnapshot(source, '2026-10-07', tuple(
        SnapshotTable(name, tuple(canonical_json(row) for row in rows)) for name, rows in inputs.items()), tuple(lookup_ids))


def changed_snapshot(prepared, name, rows):
    return replace(prepared, tables=tuple(table for table in prepared.tables if table.name != name)
                   + (SnapshotTable(name, tuple(canonical_json(row) for row in rows)),))


def plan(prepared, selection=('caro',), depths=None):
    depths = {'caro': 2} if depths is None else depths
    evaluation = evaluate_prefix(prepared.source, selection, depths)
    return plan_transition(prepared, selection, depths, evaluation)


def test_issue79_equal_depth_and_empty_selection_are_explicit_no_ops():
    prepared = prepared_snapshot()
    for selection, depths in ((('caro',), {'caro': 3}), ((), {})):
        result = plan(prepared, selection, depths)
        assert result.status == 'no_op' and not result.depth_changes
        assert result.current_steps == result.proposed_steps
        assert all(card.lifecycle == 'preserve' for card in result.cards)
        assert all(membership.action == 'preserve' for membership in result.memberships)


def test_issue79_selected_caro_routes_preserve_unselected_qgd_and_alias_decisions():
    prepared = prepared_snapshot(snapshot((line('caro'), line('caro-b', CARO_B), line('alias'), line('qgd', QGD))))
    result = plan(prepared, ('caro', 'caro-b'), {'caro': 2, 'caro-b': 2})
    assert result.status == 'ready'
    assert {change.line_id for change in result.depth_changes} == {'caro', 'caro-b'}
    for route in ('qgd', 'alias'):
        assert [step for step in result.current_steps if step.line_id == route] == [step for step in result.proposed_steps if step.line_id == route]
    alias_id = next(step.card_id for step in result.current_steps if step.line_id == 'alias')
    assert next(card for card in result.cards if card.card_id == alias_id).classification == 'unchanged'


def test_issue79_shared_card_retains_other_membership_history_queue_and_owner():
    prepared = prepared_snapshot()
    old_id = prepared.source.published_steps[0].card_id
    prepared = changed_snapshot(prepared, 'repertoire_cards', prepared.rows('repertoire_cards') + [
        dict(repertoire_id='other', card_id=old_id, canonical_route_source=1)])
    prepared = changed_snapshot(prepared, 'daily_queue', [dict(id=12, card_id=old_id, status='queued')])
    result = plan(prepared)
    retained = next(card for card in result.cards if card.card_id == old_id)
    assert retained.classification == 'retained_shared' and retained.lifecycle == 'preserve'
    assert retained.owner_after == 'other'
    assert {(link.repertoire_id, link.action) for link in result.memberships if link.card_id == old_id} == {('rep', 'obsolete'), ('other', 'preserve')}
    assert result.attempts[0].action == 'preserve'
    assert next(item for item in result.submissions if item.card_id == old_id).unresolved_submission == 'existing_identity_recovery'


def test_issue79_compatible_targets_reuse_real_history_and_keep_seed_distinct():
    prepared = prepared_snapshot()
    proposed = evaluate_prefix(prepared.source, ['caro'], {'caro': 2})
    from app.services.prefix_transition import evaluation_steps
    target = evaluation_steps(proposed['whole_repertoire']['proposed'])[0]
    prepared = changed_snapshot(prepared, 'cards', prepared.rows('cards') + [card_row(target, repertoire_id='other')])
    old_id = prepared.source.published_steps[0].card_id
    prepared = changed_snapshot(prepared, 'reviews', [dict(id=1, card_id=old_id), dict(id=2, card_id=target.card_id)])
    prepared = changed_snapshot(prepared, 'opening_card_schedule_seeds', [dict(card_id=target.card_id, source_card_id=old_id, baseline_successful_days=2)])
    result = plan(prepared)
    reused = next(card for card in result.cards if card.card_id == target.card_id)
    assert reused.classification == 'reuse_existing' and reused.review_ids == (2,)
    assert json.loads(reused.schedule_seed_json)['source_card_id'] == old_id
    assert next(card for card in result.cards if card.card_id == old_id).review_ids == (1,)
    assert all(card.new_review_count == card.new_decision_observation_count == 0 for card in result.cards)


def test_issue79_replacements_start_with_postgres_defaults_without_fabricated_evidence():
    prepared = prepared_snapshot()
    old_id = prepared.source.published_steps[0].card_id
    prepared = changed_snapshot(prepared, 'reviews', [dict(id=4, card_id=old_id)])
    result = plan(prepared)
    new = [card for card in result.cards if card.classification == 'new_replacement']
    assert len(new) == 2
    assert {json.loads(card.schedule_json)['state'] for card in new} == {'new', 'locked'}
    assert all(card.review_ids == () and card.schedule_seed_json is None for card in new)
    retired = next(card for card in result.cards if card.card_id == old_id)
    assert retired.lifecycle == 'archive' and retired.review_ids == (4,)
    assert retired.history_handling == 'retain_on_original_identity'


def test_issue79_pending_active_delayed_and_offline_attempts_have_explicit_dispositions():
    prepared = prepared_snapshot()
    old_id = prepared.source.published_steps[0].card_id
    prepared = changed_snapshot(prepared, 'daily_queue', [dict(id=5, card_id=old_id, status='queued')])
    prepared = changed_snapshot(prepared, 'queue_attempt_origins', [dict(queue_entry_id=5, card_id=old_id, revision=1)])
    prepared = changed_snapshot(prepared, 'opening_evidence_attempts', [dict(attempt_id='active', card_id=old_id, state='active'), dict(attempt_id='partial', card_id=old_id, state='partial'), dict(attempt_id='done', card_id=old_id, state='complete')])
    prepared = changed_snapshot(prepared, 'pending_commands', [dict(operation_id='delayed', card_id=old_id)])
    prepared = changed_snapshot(prepared, 'review_attempt_receipts', [dict(attempt_id='saved', card_id=old_id)])
    result = plan(prepared)
    assert {attempt.object_id: attempt.action for attempt in result.attempts} == {
        '5': 'supersede_projection', '5:1': 'retain_for_recovery', 'active': 'retire_with_conflict',
        'partial': 'retire_with_conflict', 'done': 'preserve', 'delayed': 'retire_with_conflict', 'saved': 'replay_receipt'}
    retired_submission = next(item for item in result.submissions if item.card_id == old_id)
    assert retired_submission.unresolved_submission == 'archived_identity_conflict'
    assert retired_submission.completed_receipt == 'replay_original_result' and not retired_submission.replacement_credit
    assert result.offline_visibility == 'client_local_attempts_not_enumerable'


@pytest.mark.parametrize('conflict', ['authored', 'payload', 'provenance', 'archived', 'schedule'])
def test_issue79_authored_edits_targets_and_provenance_fail_closed(conflict):
    prepared = prepared_snapshot()
    if conflict == 'authored':
        links = prepared.rows('repertoire_cards')
        next(link for link in links if link['card_id'] == prepared.source.published_steps[0].card_id)['canonical_route_source'] = 1
        prepared = changed_snapshot(prepared, 'repertoire_cards', links)
    else:
        from app.services.prefix_transition import evaluation_steps
        target = evaluation_steps(evaluate_prefix(prepared.source, ['caro'], {'caro': 2})['whole_repertoire']['proposed'])[0]
        modifications = {'payload': {'trained_color': 'white'}, 'provenance': {'canonical_route_source': 1},
                         'archived': {'archived': 1}, 'schedule': {'state': 'locked', 'introduced_at': None}}[conflict]
        prepared = changed_snapshot(prepared, 'cards', prepared.rows('cards') + [card_row(target, **modifications)])
    result = plan(prepared)
    assert result.status == 'blocked' and result.blockers
    assert all(blocker.reason for blocker in result.blockers)
    assert any(card.classification == 'conflicting' for card in result.cards)


def test_issue79_saved_split_bypass_blocks_without_repair_or_history_transfer():
    prepared = prepared_snapshot(snapshot((line('caro'),), (split(CARO_A, 4),)))
    result = plan(prepared, ('caro',), {'caro': 1})
    assert result.status == 'blocked'
    assert 'saved_split_conflict' in {blocker.code for blocker in result.blockers}
    assert plan(prepared, ('caro',), {'caro': 3}).status == 'no_op'


def test_issue79_same_snapshot_and_input_produce_deeply_immutable_identical_plans():
    prepared = prepared_snapshot()
    before = transition_snapshot_identity(prepared)
    result = plan(prepared)
    assert result.model_dump_json() == plan(prepared).model_dump_json()
    assert transition_snapshot_identity(prepared) == before
    with pytest.raises(ValidationError):
        result.cards[0].lifecycle = 'archive'
    with pytest.raises(TypeError):
        result.cards[0] = result.cards[0]
    with pytest.raises(Exception):
        result.proposed_steps[0].moves = ()


@pytest.mark.parametrize('change', ['source', 'generation', 'revision', 'membership', 'review', 'seed', 'attempt', 'new_target', 'day'])
def test_issue79_source_graph_revision_and_transition_state_invalidate_old_plan(change):
    prepared = prepared_snapshot()
    result = plan(prepared)
    validate_plan_freshness(result, prepared)
    if change == 'source':
        modified = replace(prepared, source=replace(prepared.source, lines=(replace(prepared.source.lines[0], name='edited'), *prepared.source.lines[1:])))
    elif change == 'generation':
        modified = replace(prepared, source=replace(prepared.source, graph_generation=2))
    elif change == 'day':
        modified = replace(prepared, study_day='2026-10-08')
    else:
        table_name = {'revision': 'cards', 'membership': 'repertoire_cards', 'review': 'reviews',
                      'seed': 'opening_card_schedule_seeds', 'attempt': 'daily_queue', 'new_target': 'cards'}[change]
        rows = prepared.rows(table_name)
        if change == 'revision':
            rows[0]['revision'] += 1
        else:
            rows.append({'changed': change})
        modified = changed_snapshot(prepared, table_name, rows)
    with pytest.raises(PrefixEvaluationError, match='changed') as error:
        validate_plan_freshness(result, modified)
    assert error.value.code == 'stale_plan'


@pytest.mark.parametrize('depths', [{'caro': 4}, {'caro': True}, {}, {'caro': 2, 'qgd': 2}])
def test_issue79_lengthening_and_invalid_selection_never_produce_an_applicable_plan(depths):
    prepared = prepared_snapshot()
    from app.services.prefix_transition import validate_shortening
    with pytest.raises(PrefixEvaluationError):
        validate_shortening(prepared.source, ['caro'], depths)


def test_issue79_depth_only_shortening_is_distinct_from_no_op():
    prepared = prepared_snapshot(snapshot((line('caro', depth=4),)))
    result = plan(prepared, ('caro',), {'caro': 3})
    assert result.status == 'ready' and result.depth_changes
    assert result.current_steps == result.proposed_steps
    assert all(card.lifecycle == 'preserve' for card in result.cards)


def test_issue79_shared_target_global_role_change_blocks_even_with_matching_identity():
    from app.services.prefix_transition import evaluation_steps
    prepared = prepared_snapshot()
    target = evaluation_steps(evaluate_prefix(prepared.source, ['caro'], {'caro': 2})['whole_repertoire']['proposed'])[0]
    prepared = changed_snapshot(prepared, 'cards', prepared.rows('cards') + [card_row(target, repertoire_id='other', kind='response')])
    prepared = changed_snapshot(prepared, 'repertoire_cards', prepared.rows('repertoire_cards') + [
        dict(repertoire_id='other', card_id=target.card_id, canonical_route_source=0)])
    result = plan(prepared)
    assert result.status == 'blocked'
    assert 'shared_metadata_change' in {blocker.code for blocker in result.blockers}
    assert next(link for link in result.memberships if link.card_id == target.card_id and link.repertoire_id == 'other').action == 'preserve'


def test_issue79_implicit_authored_owner_retains_card_without_explicit_other_link():
    prepared = prepared_snapshot()
    cards = prepared.rows('cards')
    old_id = prepared.source.published_steps[0].card_id
    next(card for card in cards if card['id'] == old_id).update(canonical_route_source=1, repertoire_id='other')
    prepared = changed_snapshot(prepared, 'cards', cards)
    result = plan(prepared)
    retained = next(card for card in result.cards if card.card_id == old_id)
    assert retained.classification == 'retained_shared' and retained.lifecycle == 'preserve'
    assert retained.owner_before == retained.owner_after == 'other'


def test_issue79_reordered_selection_tables_and_rows_produce_identical_plan():
    prepared = prepared_snapshot(snapshot((line('caro'), line('caro-b', CARO_B), line('qgd', QGD))))
    result = plan(prepared, ('caro', 'caro-b'), {'caro': 2, 'caro-b': 2})
    reordered = replace(prepared, tables=tuple(reversed(prepared.tables)))
    assert result == plan(reordered, ('caro-b', 'caro'), {'caro-b': 2, 'caro': 2})


def test_issue79_tampered_plan_fails_integrity_fence():
    prepared = prepared_snapshot()
    result = plan(prepared)
    with pytest.raises(PrefixEvaluationError):
        validate_plan_freshness(result.model_copy(update={'depth_changes': ()}), prepared)
