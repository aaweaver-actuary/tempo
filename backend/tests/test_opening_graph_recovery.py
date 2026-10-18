"""Maintenance claim guards; end-to-end proof lives in the regular PostgreSQL suite."""
import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import pytest
from app.services.opening_graph_recovery import claim_exact_task


def task_row(**changes):
    return {'id': 'target', 'kind': 'opening_graph_rebuild', 'deduplication_key': 'caro',
            'generation': 1, 'state': 'failed', 'phase': 'publish',
            'payload_json': json.dumps({'repertoire_id': 'caro'}), **changes}


@pytest.mark.parametrize('changes', [
    {'kind': 'repertoire_priority'}, {'deduplication_key': 'other'},
    {'generation': 2}, {'state': 'leased'}, {'phase': 'stage'},
    {'payload_json': '{"repertoire_id":"other"}'},
])
def test_maintenance_claim_refuses_wrong_scope_generation_lease_or_checkpoint(changes):
    database = Mock()
    database.execute_native.return_value.fetchone.return_value = task_row(**changes)
    with pytest.raises(ValueError):
        claim_exact_task(database, 'target', 'opening_graph_rebuild', 'caro', 1,
                         resume_failed=True, day='2026-10-15')
    assert database.execute_native.call_count == 1


def test_maintenance_preflight_and_release_yield_to_foreground(monkeypatch):
    from app.services import opening_graph_recovery as recovery
    calls = []
    def denied_then_ready():
        calls.append(True)
        if len(calls) == 1:
            raise recovery.BackgroundAdmissionDeferred('Foreground is active')
        return 'ready'
    monkeypatch.setattr(recovery.time, 'sleep', lambda _seconds: None)
    assert recovery.wait_for_admission(denied_then_ready) == 'ready'
    assert len(calls) == 2


@pytest.fixture
def selected(monkeypatch):
    from app.services import opening_graph_recovery as recovery
    day = recovery.local_study_day()
    prepared = {'mode': recovery.QUEUED_REBUILD_MODE, 'plan_version': 2, 'target': 'target-hash', 'task_id': 'target',
                'generation': 2, 'repertoire_id': 'caro', 'day': day, 'previous_graph_generation': 1,
                'scope_token': {'scope_source_revision': 197, 'canonical_prefix_revision': 0,
                                'initial_depth': 3, 'study_only_mode': 1, 'run_id': 'ranks', 'epoch': 1},
                'tasks': {'opening_graph_rebuild': {'id': 'target', 'generation': 2},
                          'integrity_scan': {'id': 'scan', 'generation': 3, 'prior': None},
                          'opening_segmentation': {'id': 'segments', 'generation': 2, 'prior': None}}}
    row = task_row(generation=2, state='queued', phase='queued', attempt_count=0,
                   lease_expires_at=None, next_attempt_at=datetime.now(timezone.utc).isoformat())
    database = Mock()
    def execute(statement, parameters=()):
        cursor = Mock()
        if statement.startswith('UPDATE background_tasks'):
            row.update(lease_token=parameters[0], lease_expires_at=parameters[1], payload_json=parameters[2], state='leased')
        cursor.fetchone.return_value = dict(row)
        return cursor
    database.execute_native.side_effect = execute
    monkeypatch.setattr(recovery, 'validate_selected_scope', Mock())
    return recovery, prepared, row, database


def claim_selected(selected):
    recovery, prepared, row, database = selected
    return recovery.claim_exact_task(database, 'target', 'opening_graph_rebuild', 'caro', 2,
                                     day=prepared['day'], selected_rebuild=prepared)


def test_selected_queued_claim_is_exact_and_retains_checkpoint_and_scope_marker(selected):
    recovery, prepared, row, database = selected
    claimed = claim_selected(selected)
    assert claimed['phase'] == 'queued'
    assert claimed['maintenance_scope'] == prepared
    assert claimed['payload']['maintenance_recovery_scope'] == recovery._selected_scope_key(prepared)
    assert recovery.validate_selected_scope.call_count == 1
    assert all('WHERE id=%s' in call.args[0] for call in database.execute_native.call_args_list
               if call.args[0].startswith('UPDATE background_tasks'))


@pytest.mark.parametrize('changes', [
    {'id': 'unrelated', 'deduplication_key': 'other'}, {'generation': 3}, {'kind': 'daily_queue'},
    {'state': 'failed', 'phase': 'publish'}, {'state': 'leased', 'lease_expires_at': '2999-01-01T00:00:00+00:00'},
    {'phase': 'stage'}, {'next_attempt_at': '2999-01-01T00:00:00+00:00'},
    {'payload_json': '{"repertoire_id":"other"}'},
    {'payload_json': '{"repertoire_id":"caro","maintenance_recovery_scope":"another-plan"}'},
])
def test_selected_queued_claim_excludes_unrelated_replacement_active_lease_and_unsafe_restart(selected, changes):
    recovery, prepared, row, database = selected
    row.update(changes)
    with pytest.raises(ValueError):
        claim_selected(selected)
    assert not any(call.args[0].startswith('UPDATE') for call in database.execute_native.call_args_list)


def test_selected_interruption_reclaims_only_expired_own_lease_and_rejects_old_delivery(selected, monkeypatch):
    from app.services import durable_tasks
    recovery, prepared, row, database = selected
    first = claim_selected(selected)
    with pytest.raises(ValueError, match='leased'):
        claim_selected(selected)
    row.update(phase='stage', lease_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    resumed = claim_selected(selected)
    assert resumed['phase'] == 'stage'
    assert resumed['lease_token'] != first['lease_token']
    database.execute.return_value.fetchone.return_value = row
    monkeypatch.setattr(durable_tasks, 'increment', Mock())
    assert not durable_tasks.lock_current_slice(database, first)
    assert durable_tasks.lock_current_slice(database, resumed)


def test_selected_owner_transaction_rechecks_scope_and_rolls_back_on_source_change(selected, monkeypatch):
    from app.services import durable_tasks
    recovery, prepared, row, database = selected
    task = claim_selected(selected)
    database.execute.return_value.fetchone.return_value = row
    recovery.validate_selected_scope.side_effect = ValueError('Source changed')
    with pytest.raises(ValueError, match='Source changed'):
        durable_tasks.lock_current_slice(database, task)
    assert recovery.validate_selected_scope.call_count == 2


@pytest.mark.parametrize('changed', ['day', 'source', 'study_only', 'depth', 'activation', 'target', 'graph', 'publication', 'transition'])
def test_selected_transaction_scope_rejects_identity_source_day_policy_and_generation_changes(monkeypatch, changed):
    from app.services import opening_graph_recovery as recovery
    identity = {'cluster_id': 'cluster', 'database_oid': '1', 'database': 'tempo', 'schema': 'public'}
    token = {'scope_source_revision': 197, 'canonical_prefix_revision': 0, 'initial_depth': 3,
             'study_only_mode': 1, 'run_id': 'ranks', 'epoch': 1}
    prepared = {'mode': recovery.QUEUED_REBUILD_MODE, 'plan_version': 2, 'day': recovery.local_study_day(), 'repertoire_id': 'caro',
                'target': recovery.stable_key(*identity.values()), 'scope_token': deepcopy(token),
                'task_id': 'target', 'generation': 2, 'previous_graph_generation': 1}
    graph = {'id': 'target', 'generation': 2, 'state': 'queued', 'phase': 'queued'}
    publication = {'generation': 1, 'state': 'ready'}
    transition = None
    if changed == 'day': prepared['day'] = '2000-01-01'
    if changed == 'source': token['scope_source_revision'] += 1
    if changed == 'study_only': token['study_only_mode'] = 0
    if changed == 'depth': token['initial_depth'] += 1
    if changed == 'activation': token['epoch'] += 1
    if changed == 'target': identity['database_oid'] = 'other'
    if changed == 'graph': graph['generation'] += 1
    if changed == 'publication': publication['generation'] = 9
    if changed == 'transition': transition = {'exists': 1}
    database = Mock()
    database.execute_native.return_value.fetchone.side_effect = [identity, token, graph, publication, transition]
    with pytest.raises(ValueError):
        recovery.validate_selected_scope(database, prepared)
    assert all(not call.args[0].startswith(('UPDATE', 'INSERT', 'DELETE'))
               for call in database.execute_native.call_args_list)


@pytest.mark.parametrize('phase', ['queued', 'stage', 'link', 'publish', 'classify', 'integrity', 'cleanup', 'finalize', 'published'])
def test_selected_scope_publication_follows_the_owner_checkpoint_and_keeps_policy_locks(phase):
    from app.services import opening_graph_recovery as recovery
    identity = {'cluster_id': 'cluster', 'database_oid': '1', 'database': 'tempo', 'schema': 'public'}
    token = {'scope_source_revision': 197, 'canonical_prefix_revision': 0, 'initial_depth': 3,
             'study_only_mode': 1, 'run_id': 'ranks', 'epoch': 1}
    prepared = {'mode': recovery.QUEUED_REBUILD_MODE, 'plan_version': 2, 'day': recovery.local_study_day(), 'repertoire_id': 'caro',
                'target': recovery.stable_key(*identity.values()), 'scope_token': token,
                'task_id': 'target', 'generation': 2, 'previous_graph_generation': 1,
                'tasks': {'integrity_scan': {'id': 'scan', 'generation': 3, 'prior': None},
                          'opening_segmentation': {'id': 'segments', 'generation': 2, 'prior': None}}}
    published = phase in {'classify', 'integrity', 'cleanup', 'finalize', 'published'}
    database = Mock()
    database.execute_native.return_value.fetchone.side_effect = [identity, token,
        {'id': 'target', 'generation': 2, 'state': 'complete' if phase == 'published' else 'queued', 'phase': phase},
        {'generation': 2 if published else 1, 'state': 'ready'}, None,
        {'id': 'scan', 'generation': 3, 'state': 'queued', 'payload_json': '{"repertoire_id":"caro","graph_generation":2}'} if phase == 'published' else None,
        None]
    recovery.validate_selected_scope(database, prepared)
    assert 'FOR SHARE OF r,s,a' in database.execute_native.call_args_list[1].args[0]
    assert 'FOR SHARE' in database.execute_native.call_args_list[2].args[0]


def test_selected_pipeline_runs_only_pinned_graph_scan_segmentation_and_retains_checkpoint_budget(selected, monkeypatch):
    from app.services import postgres_opening_segmentation
    recovery, prepared, row, database = selected
    monkeypatch.setattr(recovery, 'background_lease', nullcontext)
    monkeypatch.setattr(recovery.postgres_store, 'connection', lambda **kwargs: nullcontext(database))
    monkeypatch.setattr(recovery, 'warm_completion_sql', lambda: None)
    calls = []
    def claim(_database, task_id, kind, _repertoire, generation, **kwargs):
        calls.append((kind, task_id, generation))
        if len(calls) % 2 == 0:
            return None
        return {'id': task_id, 'kind': kind, 'generation': generation, 'phase': 'queued',
                'lease_token': 'lease', 'lease_expires_at': 'later'}
    monkeypatch.setattr(recovery, 'claim_exact_task', claim)
    handled = []
    for module, name in ((recovery, 'execute_postgres_opening_graph_slice'), (recovery, 'execute_postgres_integrity_slice'),
                          (postgres_opening_segmentation, 'execute_segmentation_slice')):
        monkeypatch.setattr(module, name, lambda task: handled.append(task['kind']))
    monkeypatch.setattr(recovery, 'read_rows', lambda statement, parameters: [{**prepared['tasks'][parameters[0]],
                         'payload_json': json.dumps({'repertoire_id': 'caro', 'graph_generation': 2})}])
    database.execute_native.side_effect = None
    database.execute_native.return_value.fetchone.side_effect = [
        {'status': 'needs_repair', 'scan_status': 'idle', 'checked_at': 'now', 'scan_generation': 'scan:3'},
        {'state': 'ready', 'graph_generation': 2, 'run_id': 'segments:2'},
    ]
    assert recovery.finish_queued_rebuild(prepared)['state'] == 'complete'
    assert handled == ['opening_graph_rebuild', 'integrity_scan', 'opening_segmentation']
    assert {kind for kind, *_ in calls} == set(prepared['tasks'])
    calls.clear()
    with pytest.raises(RuntimeError, match='budget'):
        recovery.finish_queued_rebuild(prepared, max_slices=1)
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match='budget'):
        recovery.finish_queued_rebuild(prepared, stop_requested=lambda: True)


def test_selected_integrity_publication_caps_remain_32_issues_64_card_source_blocks(monkeypatch):
    from app.services import postgres_integrity as integrity
    monkeypatch.setattr(integrity, 'lock_current_slice', lambda *args: True)
    monkeypatch.setattr(integrity, '_graph_generation_is_current', lambda *args: True)
    task = {'id': 'scan', 'generation': 3, 'payload': {'repertoire_id': 'caro', 'graph_generation': 2}}
    for counts, message in (([33], 'publication exceeds'), ([32, 65], 'card blocks exceed')):
        database = Mock()
        database.execute_native.return_value.fetchone.side_effect = [(count,) for count in counts]
        with pytest.raises(RuntimeError, match=message):
            integrity.publish_integrity_issues_in_transaction(database, task)
        assert not any(call.args[0].startswith(('UPDATE', 'INSERT', 'DELETE')) for call in database.execute_native.call_args_list)


@pytest.mark.parametrize('completion', [False, True])
def test_selected_checkpoint_postcondition_rejects_own_authoritative_source_change(selected, monkeypatch, completion):
    from app.services import durable_tasks
    recovery, prepared, row, database = selected
    task = claim_selected(selected)
    monkeypatch.setattr(durable_tasks, 'clear_queue_refresh_error_in_transaction', Mock())
    monkeypatch.setattr(durable_tasks, '_record_event', Mock())
    database.execute.return_value.rowcount = 1
    recovery.validate_selected_scope.side_effect = ValueError('Source changed during slice')
    with pytest.raises(ValueError, match='during slice'):
        if completion:
            durable_tasks.complete_task_slice_in_transaction(database, task)
        else:
            durable_tasks.advance_task_slice_in_transaction(database, task, next_phase='stage', next_payload=task['payload'])


def test_selected_cleanup_refuses_issued_or_head_entry_before_owner_removal(monkeypatch):
    from app.services import postgres_opening_graph as graph
    monkeypatch.setattr(graph, 'lock_current_slice', lambda *args: True)
    remove = Mock()
    monkeypatch.setattr(graph, 'remove_obsolete_graph_memberships', remove)
    database = Mock()
    database.execute_native.return_value.fetchone.return_value = {'protected': 1}
    task = {'generation': 2, 'maintenance_scope': {}, 'payload': {'repertoire_id': 'caro', 'local_day': '2026-10-18'}}
    with pytest.raises(ValueError, match='protected queue'):
        graph.cleanup_graph_cards_in_transaction(database, task, graph.PreparedGraphCleanupSlice(('obsolete',), 'obsolete'))
    remove.assert_not_called()


def test_selected_preservation_allows_generated_memberships_and_rejects_schedule_history_head_or_frozen_changes():
    from app.services import opening_graph_recovery as recovery
    before = {'mode': recovery.QUEUED_REBUILD_MODE, 'target': 'database', 'settings': [{'study_only_mode': 1}],
              'sources': [{'id': 'line'}], 'memberships': [{'card_id': 'old', 'canonical_route_source': 0}],
              'cards': [{'id': 'old', 'revision': 1}], 'reviews': [{'id': 1, 'rating': 'again'}],
              'issued_origins': [{'queue_entry_id': 7, 'card_id': 'old', 'revision': 1},
                                 {'queue_entry_id': 7, 'card_id': 'old', 'revision': 2}],
              'activation': {'run_id': 'ranks', 'epoch': 1}, 'frozen_ranks': [{'card_id': 'old', 'ordinal': 1}],
              'protected_schedules': [{'id': 'old', 'fsrs_card_json': 'unchanged'}],
              'protected_queue': [{'id': 7, 'position': 0, 'status': 'queued'}]}
    after = deepcopy(before)
    after['memberships'] = [{'card_id': 'new', 'canonical_route_source': 0}]
    after['cards'].append({'id': 'new', 'revision': 1})
    assert recovery.verify_preservation(before, after)['passed']
    for component in ('protected_schedules', 'reviews', 'protected_queue', 'frozen_ranks'):
        changed = deepcopy(after)
        changed[component][0]['changed'] = True
        assert not recovery.verify_preservation(before, changed)['passed'], component


def test_selected_queued_preflight_is_read_only_pins_followup_generations_and_rejects_newer_staging(selected, monkeypatch):
    recovery, prepared, row, database = selected
    monkeypatch.setattr(recovery, 'target_id', lambda: prepared['target'])
    monkeypatch.setattr(recovery, 'scope_token', lambda repertoire: prepared['scope_token'])
    monkeypatch.setattr(recovery, 'background_lease', nullcontext)
    connections = []
    def connection(**kwargs):
        connections.append(kwargs)
        return nullcontext(database)
    monkeypatch.setattr(recovery.postgres_store, 'connection', connection)
    staging = []
    def read(statement, parameters=()):
        if 'WHERE id=%s' in statement:
            return [dict(row)]
        if 'opening_graph_publications' in statement:
            return [{'generation': 1, 'state': 'ready'}]
        if 'opening_graph_steps' in statement:
            return staging
        kind = parameters[0]
        return [{'id': prepared['tasks'][kind]['id'], 'generation': 2 if kind == 'integrity_scan' else 1, 'state': 'complete',
                 'phase': 'published', 'lease_token': None, 'lease_expires_at': None}]
    monkeypatch.setattr(recovery, 'read_rows', read)
    arguments = ('target', 'caro', 2, 'target-hash')
    keywords = {'source_revision': 197, 'prefix_revision': 0, 'day': prepared['day']}
    plan = recovery.preflight_queued_rebuild(*arguments, **keywords)
    assert plan['tasks']['integrity_scan']['generation'] == 3
    assert plan['tasks']['integrity_scan']['prior']['generation'] == 2
    assert plan['tasks']['opening_segmentation']['generation'] == 2
    assert plan['tasks']['opening_segmentation']['prior']['generation'] == 1
    assert connections == [{'read_only': True, 'authoritative': True, 'background': True}]
    assert database.execute_native.call_count == 0
    staging.append({'exists': 1})
    with pytest.raises(ValueError, match='newer staging'):
        recovery.preflight_queued_rebuild(*arguments, **keywords)


def test_selected_cli_defaults_to_dry_run_and_resume_cannot_change_pins(selected, monkeypatch, tmp_path):
    recovery, prepared, row, database = selected
    path = Path(__file__).resolve().parents[2] / 'scripts/recover-opening-publication.py'
    spec = importlib.util.spec_from_file_location('selected_rebuild_cli', path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'preflight_queued_rebuild', Mock(return_value=prepared))
    monkeypatch.setattr(cli, 'capture_evidence', Mock(return_value={'private': 'evidence'}))
    execute = Mock()
    monkeypatch.setattr(cli, 'finish_queued_rebuild', execute)
    monkeypatch.setattr(recovery, 'background_lease', nullcontext)
    connections = []
    def connection(**kwargs):
        connections.append(kwargs)
        return nullcontext(database)
    monkeypatch.setattr(recovery.postgres_store, 'connection', connection)
    args = SimpleNamespace(mode=recovery.QUEUED_REBUILD_MODE, task_id='target', repertoire_id='caro', generation=2,
                           target='target-hash', day=prepared['day'], source_revision=197, prefix_revision=0,
                           output=str(tmp_path / 'private'), resume=False, apply=False, max_seconds=1200, max_slices=5)
    cli.selected_rebuild_main(args)
    execute.assert_not_called()
    assert connections == [{'read_only': True, 'authoritative': True, 'background': True}]
    assert json.loads((Path(args.output) / 'preflight.json').read_text()) == prepared
    args.resume = True
    args.source_revision = 198
    with pytest.raises(ValueError, match='differs from command'):
        cli.selected_rebuild_main(args)
    execute.assert_not_called()


def test_selected_evidence_pages_every_issued_revision_across_a_shared_queue_entry_boundary(monkeypatch):
    from app.services import opening_graph_recovery as recovery
    origins = [{'queue_entry_id': 1, 'card_id': 'card', 'revision': revision} for revision in range(1, 130)]
    pages = []
    def read(statement, parameters=()):
        if 'frozen_opening_activation' in statement:
            return [{'run_id': 'ranks'}]
        if 'queue_attempt_origins' in statement:
            pages.append(parameters)
            return origins[:128] if len(pages) == 1 else origins[128:]
        return []
    monkeypatch.setattr(recovery, 'read_rows', read)
    monkeypatch.setattr(recovery, 'target_identity', lambda: {'database': 'fixture'})
    evidence = recovery.capture_evidence('caro', recovery.local_study_day(), selected_rebuild=True)
    assert evidence['issued_origins'] == origins
    assert pages == [(0, '', 0), (1, 'card', 128)]


@pytest.mark.parametrize('kind', ['integrity_scan', 'opening_segmentation'])
@pytest.mark.parametrize('change', ['leased', 'generation', 'identity', 'phase'])
def test_selected_followup_predecessor_is_locked_and_drift_refused_before_owner_mutation(selected, kind, change):
    recovery, prepared, row, database = selected
    prior = {'id': 'old', 'generation': 1, 'state': 'queued', 'phase': 'queued', 'lease_token': None, 'lease_expires_at': None}
    prepared['tasks'][kind]['prior'] = prior
    actual = {**prior}
    if change == 'leased': actual.update(state='leased', lease_token='foreign', lease_expires_at='later')
    if change == 'generation': actual['generation'] += 1
    if change == 'identity': actual['id'] = 'replacement'
    if change == 'phase': actual['phase'] = 'scan'
    database.execute_native.side_effect = None
    database.execute_native.return_value.fetchone.return_value = actual
    with pytest.raises(ValueError, match='predecessor changed'):
        recovery._validate_followup(database, prepared, kind, prior=True, lock=True)
    assert database.execute_native.call_count == 1
    assert 'FOR UPDATE' in database.execute_native.call_args.args[0]


@pytest.mark.parametrize('absent_race', [False, True])
def test_selected_handoff_upsert_never_overwrites_a_changed_or_newly_arriving_lease(selected, monkeypatch, absent_race):
    from app.services import durable_tasks
    recovery, prepared, row, database = selected
    prior = None if absent_race else {'id': 'scan', 'generation': 2, 'state': 'complete', 'phase': 'published',
                                     'lease_token': None, 'lease_expires_at': None}
    prepared['tasks']['integrity_scan']['prior'] = prior
    task = claim_selected(selected)
    task['phase'] = 'finalize'
    recovery.validate_selected_delivery(database, task)
    assert database._selected_rebuild_handoff['kind'] == 'integrity_scan'
    events = Mock()
    monkeypatch.setattr(durable_tasks, '_record_event', events)
    database.execute.return_value.fetchone.side_effect = ([None, None] if absent_race else
        [{**prior, 'state': 'leased', 'lease_token': 'foreign', 'lease_expires_at': 'later', 'created_at': 'earlier'}])
    with pytest.raises(ValueError):
        durable_tasks.enqueue_task_in_transaction(database, 'integrity_scan', 'caro',
            {'repertoire_id': 'caro', 'graph_generation': 2}, minimum_generation=2)
    if absent_race:
        assert ' WHERE FALSE RETURNING *' in database.execute.call_args_list[1].args[0]
    else:
        assert database.execute.call_count == 1
    events.assert_not_called()


def test_selected_handoff_upsert_uses_prior_fence_and_checks_the_owner_result(selected, monkeypatch):
    from app.services import durable_tasks
    recovery, prepared, row, database = selected
    prior = {'id': 'scan', 'generation': 2, 'state': 'complete', 'phase': 'published', 'lease_token': None, 'lease_expires_at': None}
    prepared['tasks']['integrity_scan']['prior'] = prior
    task = claim_selected(selected); task['phase'] = 'finalize'
    recovery.validate_selected_delivery(database, task)
    monkeypatch.setattr(durable_tasks, '_record_event', Mock())
    queued = {**prior, 'generation': 3, 'state': 'queued', 'phase': 'queued', 'replaced_pending_generation': False,
              'payload_json': json.dumps({'repertoire_id': 'caro', 'graph_generation': 2, 'local_day': prepared['day']})}
    database.execute.return_value.fetchone.side_effect = [{**prior, 'created_at': 'earlier'}, queued, queued]
    result = durable_tasks.enqueue_task_in_transaction(database, 'integrity_scan', 'caro',
        {'repertoire_id': 'caro', 'graph_generation': 2, 'local_day': prepared['day']}, minimum_generation=2)
    assert result['generation'] == 3
    statement, parameters = database.execute.call_args_list[1].args
    assert durable_tasks._SELECTED_REBUILD_UPSERT_FENCE in statement
    assert parameters[-4:] == ('scan', 2, 'complete', 'published')


@pytest.mark.parametrize('fence,placeholder_count', [('existing', 14), ('absent', 10)])
def test_selected_handoff_fence_retains_native_parameter_binding(fence, placeholder_count):
    from app import postgres_store
    from app.services import durable_tasks
    predicate = durable_tasks._SELECTED_REBUILD_UPSERT_FENCE if fence == 'existing' else ' WHERE FALSE'
    translated = postgres_store.postgres_sql(durable_tasks._TASK_UPSERT_SQL + predicate + ' RETURNING *')
    assert translated.count('%s') == placeholder_count
    assert 'RETURNING *' in translated and ' WHERE ' in translated


@pytest.mark.parametrize('change', ['generation', 'identity', 'leased', 'repertoire', 'graph', 'day'])
def test_selected_handoff_checks_returned_identity_lease_and_payload_before_commit(selected, change):
    recovery, prepared, row, database = selected
    payload = {'repertoire_id': 'caro', 'graph_generation': 2, 'local_day': prepared['day']}
    actual = dict(payload)
    returned = {'id': 'scan', 'generation': 3, 'state': 'queued', 'phase': 'queued', 'lease_token': None, 'lease_expires_at': None}
    if change == 'generation': returned['generation'] = 4
    if change == 'identity': returned['id'] = 'other'
    if change == 'leased': returned.update(state='leased', lease_token='foreign')
    if change == 'repertoire': actual['repertoire_id'] = 'other'
    if change == 'graph': actual['graph_generation'] = 1
    if change == 'day': actual['local_day'] = '2000-01-01'
    returned['payload_json'] = json.dumps(actual)
    handoff = {'kind': 'integrity_scan', 'repertoire_id': 'caro', 'pin': prepared['tasks']['integrity_scan'],
               'graph_generation': 2, 'day': prepared['day']}
    with pytest.raises(ValueError, match='Owner enqueue differs'):
        recovery.validate_selected_enqueue(handoff, 'integrity_scan', 'caro', returned, payload, after=True)


def test_selected_execution_baseline_rebases_live_preflight_then_is_immutable_on_resume(selected, monkeypatch, tmp_path):
    recovery, prepared, row, database = selected
    path = Path(__file__).resolve().parents[2] / 'scripts/recover-opening-publication.py'
    spec = importlib.util.spec_from_file_location('quiesced_baseline_cli', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    preflight = {'mode': recovery.QUEUED_REBUILD_MODE, 'target': 'target', 'settings': [], 'sources': [],
                 'memberships': [], 'cards': [], 'reviews': [{'id': 1, 'rating': 'again'}], 'issued_origins': [],
                 'activation': {}, 'frozen_ranks': [], 'protected_schedules': [{'id': 'card', 'fsrs_card_json': 'old'}],
                 'protected_queue': [{'id': 7, 'position': 0}]}
    (tmp_path / 'before.json').write_text(json.dumps(preflight))
    current = deepcopy(preflight)
    current['reviews'].append({'id': 2, 'rating': 'correct'})
    current['protected_schedules'][0]['fsrs_card_json'] = 'reviewed between dry-run and downtime'
    current['protected_queue'] = [{'id': 8, 'position': 0}]
    assertion = Mock()
    monkeypatch.setattr(cli, 'assert_selected_execution_not_started', assertion)
    monkeypatch.setattr(cli, 'capture_evidence', lambda *args, **kwargs: deepcopy(current))
    baseline = cli.prepare_execution_baseline(tmp_path, prepared)
    saved = (tmp_path / 'execution-before.json').read_bytes()
    assert baseline['reviews'] == current['reviews'] and assertion.call_count == 2
    assert json.loads((tmp_path / 'before.json').read_text()) == preflight
    assertion.reset_mock()
    assert cli.prepare_execution_baseline(tmp_path, prepared) == baseline
    assertion.assert_not_called()
    assert (tmp_path / 'execution-before.json').read_bytes() == saved
    current['protected_schedules'][0]['fsrs_card_json'] = 'unexpected later review'
    with pytest.raises(ValueError, match='Protected evidence changed before resume'):
        cli.prepare_execution_baseline(tmp_path, prepared)
    assert (tmp_path / 'execution-before.json').read_bytes() == saved


def test_selected_execution_baseline_cannot_be_created_after_partial_writes(selected, monkeypatch, tmp_path):
    recovery, prepared, row, database = selected
    path = Path(__file__).resolve().parents[2] / 'scripts/recover-opening-publication.py'
    spec = importlib.util.spec_from_file_location('missing_baseline_cli', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    capture = Mock()
    monkeypatch.setattr(cli, 'capture_evidence', capture)
    monkeypatch.setattr(cli, 'assert_selected_execution_not_started', Mock(side_effect=ValueError('Execution baseline is missing after maintenance started')))
    with pytest.raises(ValueError, match='after maintenance started'):
        cli.prepare_execution_baseline(tmp_path, prepared)
    capture.assert_not_called()
    assert not (tmp_path / 'execution-before.json').exists()


@pytest.mark.parametrize('started', ['no', 'marker', 'phase', 'event'])
def test_selected_execution_baseline_requires_an_authoritatively_unstarted_graph(selected, monkeypatch, started):
    recovery, prepared, row, database = selected
    monkeypatch.setattr(recovery, 'background_lease', nullcontext)
    monkeypatch.setattr(recovery.postgres_store, 'connection', lambda **kwargs: nullcontext(database))
    saved = {**row}
    if started == 'marker': saved['payload_json'] = '{"repertoire_id":"caro","maintenance_recovery_scope":"saved"}'
    if started == 'phase': saved['phase'] = 'stage'
    database.execute_native.side_effect = None
    database.execute_native.return_value.fetchone.side_effect = [saved, {'claimed': 1} if started == 'event' else None]
    if started == 'no':
        recovery.assert_selected_execution_not_started(prepared)
    else:
        with pytest.raises(ValueError, match='baseline is missing after maintenance started'):
            recovery.assert_selected_execution_not_started(prepared)


def native_recovery_fixture_module():
    path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_opening_graph_recovery.py'
    spec = importlib.util.spec_from_file_location('native_recovery_fixture_isolation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_recovery_fixture_isolates_reused_physical_cards_per_scenario(monkeypatch):
    native = native_recovery_fixture_module()
    moves = ['e2e4', 'c7c6', 'd2d4', 'd7d5', 'e4e5', 'c8f5', 'g1f3']
    line = {'id': 'line', 'start_fen': native.chess.STARTING_FEN,
            'moves_json': json.dumps(moves), 'trained_color': 'white'}
    first = native.build_graph(native.GraphInput('scenario-one', (line,), 3))
    second = native.build_graph(native.GraphInput('scenario-two', ({**line, 'id': 'other-line'},), 3))
    assert first[0].card_id == second[0].card_id  # A repertoire UUID is not card isolation.
    administrator = Mock()
    monkeypatch.setattr(native.psycopg, 'connect', lambda *args, **kwargs: nullcontext(administrator))
    migrations = Mock();monkeypatch.setattr(native, '_apply_scenario_migrations', migrations)
    close_pools = Mock();monkeypatch.setattr(native.postgres_store, 'close_pools', close_pools)
    tokens = iter(['a' * 12, 'b' * 12]);monkeypatch.setattr(native.secrets, 'token_hex', lambda count: next(tokens))
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', 'original-reader')
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', 'original-writer')
    urls = []
    for _ in range(2):
        with native.isolated_scenario_database('postgresql://postgres@fixture/postgres') as url:
            urls.append(url)
            assert native.os.environ['TEMPO_DATABASE_READ_URL'] == url
            assert native.os.environ['TEMPO_DATABASE_WRITE_URL'] == url
    assert urls[0] != urls[1]
    assert [call.args for call in migrations.call_args_list] == [(urls[0],), (urls[1],)]
    statements = [call.args[0].as_string() for call in administrator.execute.call_args_list]
    for token in ['a' * 12, 'b' * 12]:
        name = 'tempo_caro_recovery_scenario_' + token
        assert statements.count('CREATE DATABASE "' + name + '"') == 1
        assert statements.count('DROP DATABASE "' + name + '"') == 1
    assert close_pools.call_count == 4
    assert native.os.environ['TEMPO_DATABASE_READ_URL'] == 'original-reader'
    assert native.os.environ['TEMPO_DATABASE_WRITE_URL'] == 'original-writer'


def test_native_recovery_fixture_drops_only_its_owned_database_after_case_failure(monkeypatch):
    native = native_recovery_fixture_module()
    administrator = Mock()
    monkeypatch.setattr(native.psycopg, 'connect', lambda *args, **kwargs: nullcontext(administrator))
    monkeypatch.setattr(native, '_apply_scenario_migrations', Mock())
    monkeypatch.setattr(native.postgres_store, 'close_pools', Mock())
    monkeypatch.setattr(native.secrets, 'token_hex', lambda count: 'c' * 12)
    monkeypatch.delenv('TEMPO_TEST_INSTANCE', raising=False)
    with pytest.raises(ValueError, match='owned disposable fixture'):
        with native.isolated_scenario_database('postgresql://postgres@fixture/postgres'):
            raise AssertionError('Unowned fixture entered')
    administrator.execute.assert_not_called()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', 'original-reader')
    with pytest.raises(RuntimeError, match='original case failure'):
        with native.isolated_scenario_database('postgresql://postgres@fixture/postgres'):
            raise RuntimeError('original case failure')
    statements = [call.args[0].as_string() for call in administrator.execute.call_args_list]
    assert statements == ['CREATE DATABASE "tempo_caro_recovery_scenario_' + 'c' * 12 + '"',
                          'DROP DATABASE "tempo_caro_recovery_scenario_' + 'c' * 12 + '"']
    assert native.os.environ['TEMPO_DATABASE_READ_URL'] == 'original-reader'


def test_native_recovery_runner_enters_a_fresh_scenario_scope_before_each_case(monkeypatch):
    from contextlib import contextmanager
    native = native_recovery_fixture_module()
    names = ('test_failed_first_publication_recovery_unlocks_practiced_parent_and_admits_maia_rank',
             'test_recovery_refuses_mismatched_source_and_target',
             'test_scoped_recovery_claim_preserves_publish_and_fences_other_tasks',
             'test_selected_queued_rebuild_restart_matches_scan_segmentation_and_preserves_protected_data',
             'test_selected_rebuild_rejects_replacement_generation_source_day_and_unrelated_claim',
             'test_selected_slice_source_postcondition_rolls_back_staging_and_checkpoint')
    scopes, calls, closed = [], [], []
    @contextmanager
    def scope(url):
        current = url + '-scenario-' + str(len(scopes));scopes.append(current)
        try: yield current
        finally: closed.append(current)
    monkeypatch.setattr(native, 'isolated_scenario_database', scope)
    for name in names:
        def case(url, identity=name): calls.append((identity, url))
        case.__name__ = name
        monkeypatch.setattr(native, name, case)
    native.run_cases('owned-fixture')
    assert [name for name, _ in calls] == list(names)
    assert [url for _, url in calls] == scopes == closed
    assert len(set(scopes)) == 6
