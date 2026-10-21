"""Legacy defaults stay outside the strict, installed scheduling package."""
from datetime import date, datetime, timezone
import importlib
from importlib import metadata
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tomllib

from fsrs import Card
import fsrs.card
import pytest
import tempo_scheduling
from app.services import scheduler

REVIEWED_AT = datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)
INITIAL_CARD = Card(card_id=1, due=REVIEWED_AT).to_json()
LOCAL_DAY = date(2026, 10, 14)


@pytest.mark.parametrize('distribution_name,module_name,package_directory', [
    ('tempo-scheduling', 'tempo_scheduling', 'scheduling'),
    ('tempo-chess-core', 'tempo_chess_core', 'chess-core'),
])
def test_backend_declares_installed_noneditable_pure_packages(
    distribution_name, module_name, package_directory,
):
    repository = Path(__file__).resolve().parents[2]
    backend_project = tomllib.loads((repository / 'backend/pyproject.toml').read_text())
    package_project = tomllib.loads(
        (repository / 'packages' / package_directory / 'pyproject.toml').read_text()
    )
    distribution = metadata.distribution(distribution_name)
    assert distribution.version == package_project['project']['version']
    assert f'{distribution_name}=={distribution.version}' in backend_project['project']['dependencies']
    module = importlib.import_module(module_name)
    installed_entry = Path(distribution.locate_file(f'{module_name}/__init__.py')).resolve()
    assert Path(module.__file__).resolve() == installed_entry
    assert installed_entry.is_relative_to(Path(sys.prefix).resolve())
    direct_url = json.loads(distribution.read_text('direct_url.json'))
    assert not direct_url.get('dir_info', {}).get('editable', False)


def test_backend_scheduler_keeps_package_class_identity_and_equivalent_results():
    assert scheduler.Schedule is tempo_scheduling.Schedule
    assert scheduler.unlock_ready is tempo_scheduling.unlock_ready
    assert scheduler.schedule_review is not tempo_scheduling.schedule_review
    inputs = dict(fsrs_card_json=INITIAL_CARD, reviewed_at=REVIEWED_AT, review_day=LOCAL_DAY)
    assert scheduler.schedule_review('correct', **inputs) == tempo_scheduling.schedule_review('correct', **inputs)


def test_backend_scheduler_retains_entire_legacy_signature():
    parameters = inspect.signature(scheduler.schedule_review).parameters
    assert list(parameters) == ['outcome', 'interval_days', 'fsrs_card_json', 'first_correct_at',
        'reinforcement_pending', 'scheduling_mode', 'hard_correct_streak', 'recent_attempts',
        'light_first_interval_days', 'reviewed_at', 'review_day']
    assert [parameter.default for parameter in list(parameters.values())[1:]] == [
        0, None, None, False, 'normal', 0, None, 7, None, None]
    assert all(parameter.kind == inspect.Parameter.KEYWORD_ONLY for parameter in list(parameters.values())[1:])


def test_backend_scheduler_forwards_every_control_without_replacing_supplied_identity(monkeypatch):
    calls = []
    def capture(outcome, **controls):
        calls.append((outcome, controls))
        return tempo_scheduling.schedule_review(outcome, **controls)
    def reject_creation(*args, **kwargs):
        raise AssertionError('supplied state must not generate a card')
    monkeypatch.setattr(scheduler, '_schedule_review', capture)
    monkeypatch.setattr(scheduler, 'Card', reject_creation)
    controls = dict(interval_days=12, fsrs_card_json=INITIAL_CARD, first_correct_at='seen',
        reinforcement_pending=True, scheduling_mode='hard', hard_correct_streak=1,
        recent_attempts=['correct', 'again'], light_first_interval_days=11,
        reviewed_at=REVIEWED_AT, review_day=LOCAL_DAY)
    result = scheduler.schedule_review('correct', **controls)
    assert calls == [('correct', controls)]
    assert result == tempo_scheduling.schedule_review('correct', **controls)


@pytest.mark.parametrize('time_supplied,day_supplied', [(False, False), (True, False), (False, True), (True, True)])
def test_backend_defaults_keep_omitted_clock_and_explicit_clock_day_distinct(monkeypatch, time_supplied, day_supplied):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, zone):
            assert zone is timezone.utc
            return REVIEWED_AT
    class FixedDate(date):
        @classmethod
        def today(cls):
            return LOCAL_DAY
    monkeypatch.setattr(scheduler, 'datetime', FixedDatetime)
    monkeypatch.setattr(scheduler, 'date', FixedDate)
    inputs = dict(fsrs_card_json=INITIAL_CARD)
    if time_supplied:
        inputs['reviewed_at'] = REVIEWED_AT
    if day_supplied:
        inputs['review_day'] = date(2026, 10, 13)
    expected_day = inputs.get('review_day', REVIEWED_AT.date() if time_supplied else LOCAL_DAY)
    assert scheduler.schedule_review('correct', **inputs) == tempo_scheduling.schedule_review(
        'correct', fsrs_card_json=INITIAL_CARD, reviewed_at=REVIEWED_AT, review_day=expected_day)


def test_backend_normalizes_legacy_naive_time_to_utc():
    assert scheduler.schedule_review('correct', fsrs_card_json=INITIAL_CARD,
        reviewed_at=REVIEWED_AT.replace(tzinfo=None)) == tempo_scheduling.schedule_review(
            'correct', fsrs_card_json=INITIAL_CARD, reviewed_at=REVIEWED_AT, review_day=REVIEWED_AT.date())


@pytest.mark.parametrize('missing_state', [None, ''])
def test_backend_creates_and_serializes_real_fsrs_initial_identity_at_boundary(monkeypatch, missing_state):
    sleeps = []
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, zone):
            return REVIEWED_AT
    monkeypatch.setattr(fsrs.card, 'datetime', FixedDatetime)
    monkeypatch.setattr(fsrs.card, 'time', SimpleNamespace(sleep=sleeps.append))
    result = scheduler.schedule_review('correct', fsrs_card_json=missing_state,
                                       reviewed_at=REVIEWED_AT, review_day=LOCAL_DAY)
    assert sleeps == [0.001]
    assert json.loads(result.fsrs_card_json)['card_id'] == int(REVIEWED_AT.timestamp() * 1000)
    explicit = Card(card_id=int(REVIEWED_AT.timestamp() * 1000), due=REVIEWED_AT).to_json()
    assert result == tempo_scheduling.schedule_review('correct', fsrs_card_json=explicit,
                                                      reviewed_at=REVIEWED_AT, review_day=LOCAL_DAY)


def test_invalid_legacy_outcome_does_not_generate_identity(monkeypatch):
    def reject_creation(*args, **kwargs):
        raise AssertionError('invalid outcome generated identity')
    monkeypatch.setattr(scheduler, 'Card', reject_creation)
    with pytest.raises(ValueError, match='outcome must'):
        scheduler.schedule_review('unknown')


def test_legacy_identity_adapter_preserves_shared_digests_and_public_identity():
    from app.chess import cards
    import tempo_chess_core
    assert cards.card_id is tempo_chess_core.card_id
    fixtures = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/card-id-parity.json').read_text())
    packaged = json.loads((Path(__file__).resolve().parents[2] / 'packages/chess-core/tests/shared-card-id-parity.json').read_text())
    assert packaged == fixtures
    for case in fixtures:
        assert cards.card_id(case['fen'], case['moves']) == case['cardId'], case['name']
