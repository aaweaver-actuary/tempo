"""#558 preparation must close reads and reject stale complete route discovery."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import json

import chess
import pytest

from app.contracts.product_v02.repertoire import DecisionPresentation
from app.services import queue_admission_adapter as adapter
from tempo_game_evidence_contracts.legacy import decision_identity
from tempo_evidence_math import opening_position_key


@pytest.fixture
def source(monkeypatch):
    header = dict(scope_source_revision=4, canonical_prefix_revision=1,
        canonical_prefix_moves_json='[]', catalogue_generation=8, graph_generation=2,
        graph_state='ready', target_id='fixture', workspace_id='fixture', legacy_repertoire_limit=10)
    card = dict(id='target', revision=1, archived=0, pending_validation=0,
        superseded_by=None, blocked=False, deleted=False, linked_member=True, member=True,
        content_type='opening', introduced_at=None, state='new', trained_color='white',
        start_fen=chess.STARTING_FEN, moves_json='["e2e4"]')
    route = dict(id='line', repertoire_id='repertoire', trained_color='white',
                 start_fen=chess.STARTING_FEN, moves_json='["e2e4"]', oversized=False)
    graph = dict(card_id='target', line_id='line', trained_color='white',
                 starting_fen=chess.STARTING_FEN, moves_json='["e2e4"]')
    target = DecisionPresentation(decision_id=decision_identity('target',0,[]),
        repertoire_id='repertoire', position_key=opening_position_key(chess.STARTING_FEN),
        selected_response_uci='e2e4', presentation_id='target', presentation_revision=1,
        repertoire_revision=5, introduction='unintroduced', introduced_at=None,
        available=True, introduction_matches_current_content=True)
    active = [False]
    @contextmanager
    def read():
        assert not active[0]
        active[0] = True
        try: yield object()
        finally: active[0] = False
    def facts(database, repertoire_id, cards, lines, **_):
        return dict(header), [dict(route)] if lines else [], [dict(card)], [dict(graph)]
    def page(*_, **__):
        assert active[0]
        return [dict(route)]
    monkeypatch.setattr(adapter.admission_storage, 'product_source_facts', facts)
    monkeypatch.setattr(adapter.admission_storage, 'product_target_route_page', page)
    return header, card, route, target, read, active


def test_issue558_route_validation_runs_only_after_read_closes(source, monkeypatch):
    from app.services.encounter_preparation import prepare_encounter_introduction
    from app.study.introductions import encounter
    header, _, _, target, read, active = source
    original = encounter.target_alternatives
    def pure(routes, **kwargs):
        assert not active[0]
        return original(routes, **kwargs)
    monkeypatch.setattr(encounter, 'target_alternatives', pure)
    prepared = prepare_encounter_introduction(target, read=read)
    assert prepared.reason is None and not active[0]
    with pytest.raises(FrozenInstanceError): prepared.reason = 'changed'
    header['scope_source_revision'] += 1
    assert not prepared.matches(target, adapter.admission_storage.introduction_source_binding(header, source[1], [dict(card_id='target',line_id='line',trained_color='white',starting_fen=chess.STARTING_FEN,moves_json='["e2e4"]')], [source[2]]))


def test_issue558_route_insert_during_preparation_invalidates_complete_scan(source, monkeypatch):
    from app.services.encounter_preparation import prepare_encounter_introduction
    header, _, route, target, read, _ = source
    def changed(*_, **__):
        header['scope_source_revision'] += 1
        return [route]
    monkeypatch.setattr(adapter.admission_storage, 'product_target_route_page', changed)
    assert prepare_encounter_introduction(target, read=read).reason == 'current_source_changed_or_busy'


def test_issue558_prepared_writer_never_discovers_routes_or_replays_chess(source, monkeypatch):
    from app.services.encounter_preparation import prepare_encounter_introduction
    header, card, route, target, read, _ = source
    prepared = prepare_encounter_introduction(target, read=read)
    class Database:
        read_only = False
        class raw:
            class info:
                class transaction_status: name = 'INTRANS'
    monkeypatch.setattr(adapter.admission_storage, 'lock_product_admission', lambda *a: True)
    monkeypatch.setattr(adapter.admission_storage, 'product_encounter_budget_facts', lambda *a: ((0,10),(0,0,0)))
    monkeypatch.setattr(adapter.admission_storage, 'has_missing_due_card', lambda *a: True)
    monkeypatch.setattr(adapter.CurrentEncounterIntroductions, '_routes', lambda *a: pytest.fail('writer scanned repertoire'))
    monkeypatch.setattr(adapter, '_playable_introduction', lambda *a, **k: pytest.fail('writer replayed chess'))
    receiver = adapter.current_owner_encounter_introductions(Database(), allowance='encounter_exception',
        paused=False, prepared_introduction=prepared)
    assert receiver.admit_encounter('2026-10-29',repertoire_id='repertoire',decision_id=target.decision_id,target=target).reason == 'due_queue_refresh_pending'
    header['catalogue_generation'] += 1
    assert receiver.admit_encounter('2026-10-29',repertoire_id='repertoire',decision_id=target.decision_id,target=target).reason == 'current_source_changed_or_busy'


def test_issue558_complete_large_route_scan_closes_every_page_before_chess(source, monkeypatch):
    from app.services.encounter_preparation import prepare_encounter_introduction
    _, _, route, target, read, active = source
    unrelated_start = chess.Board()
    unrelated_start.push_uci('d2d4'); unrelated_start.push_uci('d7d5')
    rows = [dict(route, id=f'unrelated-{index:04}', start_fen=unrelated_start.fen(),
                 moves_json='["c2c4","e7e6"]') for index in range(1024)] + [route]
    rows.sort(key=lambda row: row['id'])
    pages = []
    def page(database, repertoire, after):
        assert active[0]
        selected = [row for row in rows if row['id'] > after][:16]
        pages.append(len(selected))
        return selected
    monkeypatch.setattr(adapter.admission_storage, 'product_target_route_page', page)
    original = chess.Board.push
    def outside_read(board, move):
        assert not active[0], 'pure chess work held a database section'
        return original(board, move)
    monkeypatch.setattr(chess.Board, 'push', outside_read)
    prepared = prepare_encounter_introduction(target, read=read)
    assert prepared.reason is None and prepared.route_ids == ('line',)
    assert sum(pages) == 1025 and max(pages) == 16


def test_issue558_due_refresh_preserves_active_generation_and_checkpoint(monkeypatch):
    from app.services.queue_application import ensure_due_refresh_in_transaction
    from tempo_legacy_database.adapters.commands import queue
    active = dict(id='queue', generation=7, state='leased', payload_json='{"queue_date":"2026-10-29","after":"retained"}')
    monkeypatch.setattr(queue, 'lock_current_queue_task', lambda _: active)
    from app.services import queue_application
    monkeypatch.setattr(queue_application, 'request_queue_refresh_in_transaction', lambda *a: pytest.fail('active queue generation was replaced'))
    ensure_due_refresh_in_transaction(object(), '2026-10-29')
    assert active['generation'] == 7 and json.loads(active['payload_json'])['after'] == 'retained'


def test_issue558_diagnostics_exclude_exception_message_and_payload(caplog):
    from app.services.feedback_diagnostics import diagnose_feedback, feedback_stage, feedback_failure
    import logging
    @diagnose_feedback
    def invocation():
        feedback_stage('effect_writer', dict(id='synthetic', generation=3, phase='scan', payload='private payload'))
        try:
            raise RuntimeError('private database URL')
        except RuntimeError as error:
            feedback_failure(error)
        return False
    with caplog.at_level(logging.INFO, logger='tempo.study.feedback'):
        assert invocation() is False
    record = json.loads(caplog.records[-1].getMessage().split(' ',1)[1])
    assert record['failure_stage'] == 'effect_writer' and record['error_class'] == 'RuntimeError'
    assert record['task_id'] == 'synthetic' and record['generation'] == 3
    assert 'private' not in caplog.text
