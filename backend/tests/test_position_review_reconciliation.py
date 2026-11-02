"""Adversarial review orchestration; native transaction proof is separate."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
import tempo_learning_review as review

_path = Path(__file__).resolve().parents[2] / 'services/learning/application/tests/test_application.py'
_spec = importlib.util.spec_from_file_location('position_reconciliation_capabilities', _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


class PositionStore(_fixture.Capabilities):
    def begin_position_review(self, command):
        return self.saved_receipt is None

    def lock_position_attempt(self, command):
        self.event('position_lock')

    def record_position_review(self, command, context, *, outcome, guided):
        self.event('human_review')
        return {**self.scheduled, 'requeue_today': False, 'requeue_after_cards': None}

    def save_position_receipt(self, command, context, result):
        self.save_receipt(command, context, result)

    def apply_position_effects(self, command, context, result):
        self.event('position_effects')

    def finish_position_receipt(self, command, context, result):
        self.event('finish_receipt')


def names(store):
    return [call[0] for call in store.calls]


def test_original_receipt_replay_and_changed_completion_clock_conflict():
    store = PositionStore()
    command = _fixture.command(completion=_fixture.completion(), recorded_at=_fixture.NOW.isoformat())
    first = review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
    assert review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict() == first
    assert names(store).count('human_review') == names(store).count('position_effects') == 1
    assert not {'schedule', 'snapshot', 'requeue'} & set(names(store))
    later = replace(command, recorded_at='2026-10-15T02:31:00+00:00')
    later = replace(later, payload_json=json.dumps({**json.loads(command.payload_json), 'recorded_at': later.recorded_at}))
    with pytest.raises(review.ReviewConflict) as caught:
        review.submit_review(store, later, lambda: replace(_fixture.CONTEXT, reviewed_at=_fixture.NOW.replace(minute=31)))
    assert caught.value.code == 'review_attempt_reused'
    assert names(store).count('position_effects') == 1


def test_competing_attempt_cannot_reconcile_a_completed_position_as_card_review():
    store = PositionStore()
    store.attempt = replace(store.attempt, status='complete', review_result_json=json.dumps({
        '_request': {'outcome': 'correct', 'guided': False},
        '_attempt_id': 'other-tab', '_recorded_at': _fixture.NOW.isoformat()}))
    with pytest.raises(review.ReviewConflict) as caught:
        review.apply_review(store, _fixture.command(recorded_at=_fixture.NOW.isoformat()),
                            _fixture.CONTEXT, position=True)
    assert caught.value.code == 'position_attempt_conflict'
    assert not {'schedule', 'snapshot', 'reconcile', 'human_review', 'position_effects'} & set(names(store))


def test_recovered_position_attempt_does_not_chronologically_rewrite_physical_schedule():
    store = PositionStore()
    store.attempt = None
    store.later = True
    command = _fixture.command(completion=_fixture.completion(), recorded_at=_fixture.NOW.isoformat())
    result = review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
    assert result['reconciliation'] == 'queue_origin'
    assert names(store).count('human_review') == names(store).count('position_effects') == 1
    assert not {'schedule', 'snapshot', 'reconcile', 'requeue'} & set(names(store))


def test_position_completion_requires_evidence_before_any_review_effect():
    store = PositionStore()
    with pytest.raises(review.ReviewFailure) as caught:
        review.submit_review(store, _fixture.command(), lambda: _fixture.CONTEXT)
    assert caught.value.code == 'opening_evidence_conflict'
    assert not {'schedule', 'human_review', 'position_effects', 'receipt'} & set(names(store))


def test_issued_manifest_accepts_fen_en_passant_target_without_legal_capture():
    """An issued e4 e5 route must remain reviewable with full-FEN e6."""
    from app.services.postgres_opening_evidence import issued_position_manifest
    from tests.test_position_issued_plan_contracts import option, request_for, select_and_freeze

    route = option('italian-knight-attack')
    decisions = list(route.decisions)
    assert ' KQkq - ' in decisions[1].fen
    decisions[1] = replace(decisions[1], fen=decisions[1].fen.replace(' KQkq - ', ' KQkq e6 '))
    plan = select_and_freeze(request_for(replace(route, decisions=tuple(decisions)), target_ply=2))
    manifest = issued_position_manifest(plan, {'manifest_version': 1, 'policy_version': 1,
        'presentation_snapshot_id': 1, 'repertoire_id': 'rep-italian', 'card_id': 'legacy-card-1',
        'card_revision': 4, 'trained_color': 'white'})
    assert manifest['decisions'][1]['fen'] == decisions[1].fen
