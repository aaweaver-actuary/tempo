"""Select one real owner responsibility before expensive foreground preparation."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.product_composition import next_card_priority, provider_priority
from app.study import game_feedback


NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def priority(moment="2026-10-30T11:00:00Z", stream="forward", identity="accepted"):
    return dict(urgent_determined_at=moment, urgent_stream=stream, urgent_effect_id=identity)


@pytest.mark.parametrize("delivery_priority,selected", [
    (priority("2026-10-30T12:00:00Z"), False),
    (priority("2026-10-30T10:00:00Z"), True),
    (priority(stream="reconciliation"), False),
    (priority(identity="z-later-tie"), False),
    (priority(identity="a-earlier-tie"), True),
    (None, True),  # An owner control always precedes actionable preparation.
])
def test_later_optional_delivery_is_not_prepared_before_earlier_accepted_work(
        monkeypatch, delivery_priority, selected):
    accepted_priority = priority(identity="m-accepted")
    original = SimpleNamespace(control=object() if delivery_priority is None else None,
        stream=delivery_priority and delivery_priority['urgent_stream'],
        determined_at=delivery_priority and delivery_priority['urgent_determined_at'],
        command=SimpleNamespace(key=object()))
    calls, result = [], object()
    @contextmanager
    def connections():
        yield object()
    monkeypatch.setattr('app.product_composition.managed_runtime.current_managed_runtime_identity', lambda: 'boot')
    monkeypatch.setattr('tempo_legacy_database.adapters.games.product_feedback.PostgresGamesStore',
        lambda _connections: SimpleNamespace(pending_delivery=lambda *args, **kwargs: original))
    monkeypatch.setattr(game_feedback, 'effect_id', lambda _key: delivery_priority and delivery_priority['urgent_effect_id'])
    monkeypatch.setattr(provider_priority, 'prepare_delivery_feedback',
        lambda delivery, **kwargs: calls.append(delivery) or result)
    actual = provider_priority.prepare_determined_feedback(workspace='test', connections=connections,
        clock=SimpleNamespace(now=lambda: NOW.timestamp()), admitted=lambda: True,
        before_priority=accepted_priority)
    assert actual is (result if selected else None)
    assert calls == ([original] if selected else [])


def test_accepted_work_is_selected_before_local_handoff_preparation(monkeypatch):
    events, registration = [], {}
    observed = SimpleNamespace(key=object(), qualification=None,
        progress=SimpleNamespace(determined_at='2026-10-30T11:00:00Z', stream='forward'))
    effect = object()
    @contextmanager
    def read():
        yield object()
    monkeypatch.setattr(next_card_priority, 'installed', lambda _db: True)
    monkeypatch.setattr(next_card_priority, 'feedback_preparation_admitted', lambda _db: True)
    monkeypatch.setattr(next_card_priority, 'PostgresGameFeedbackStore',
        lambda _db: SimpleNamespace(study_day=lambda _now: date(2026, 10, 30)))
    monkeypatch.setattr(next_card_priority, 'next_feedback_preparation',
        lambda *args, **kwargs: events.append('accepted_selection') or observed)
    monkeypatch.setattr(next_card_priority, 'prepare_feedback_effect',
        lambda *args, **kwargs: events.append('accepted_preparation') or effect)
    monkeypatch.setattr(game_feedback, 'effect_id', lambda _key: 'accepted')
    monkeypatch.setattr(next_card_priority, 'configure_next_card_work', lambda **kwargs: registration.update(kwargs))
    def local(*, before_priority=None):
        events.append(('local_selection', before_priority))
        return None
    next_card_priority.register_current_next_card_priority(read=read, clock=lambda: NOW, local_handoff_prepare=local)
    actual = registration['prepare']()
    assert actual.effect is effect
    assert events == ['accepted_selection', ('local_selection', priority()), 'accepted_preparation']


@pytest.mark.parametrize("terminal", [False, True])
def test_preparation_receipt_reports_retained_hold_honestly(monkeypatch, terminal):
    effect = SimpleNamespace(prepared_introduction=None, reason='first_departure_source_unavailable', terminal=terminal)
    applied = []
    monkeypatch.setattr(next_card_priority, 'feedback_preparation_admitted', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(next_card_priority, 'apply_prepared_feedback_effect',
        lambda _db, value, **kwargs: applied.append(value))
    prepared = next_card_priority.PreparedCurrentStudyWork(effect, True, lambda: NOW, priority())
    result = prepared.apply(object())
    assert applied == [effect]
    assert result.progressed is False and result.pending is (not terminal)
    assert result.reason == effect.reason


def test_unqualified_retained_work_keeps_its_original_historical_priority(monkeypatch):
    observed = SimpleNamespace(key=object(), qualification=None, progress=None,
        encounter=SimpleNamespace(detected_at='2026-10-29T11:00:00Z'), lapse=None, received_at=NOW)
    registration, selected = {}, []
    @contextmanager
    def read():
        yield object()
    monkeypatch.setattr(next_card_priority, 'installed', lambda _db: True)
    monkeypatch.setattr(next_card_priority, 'feedback_preparation_admitted', lambda _db: True)
    monkeypatch.setattr(next_card_priority, 'PostgresGameFeedbackStore',
        lambda _db: SimpleNamespace(study_day=lambda _now: date(2026, 10, 30)))
    monkeypatch.setattr(next_card_priority, 'next_feedback_preparation', lambda *args, **kwargs: observed)
    monkeypatch.setattr(next_card_priority, 'prepare_feedback_effect', lambda *args, **kwargs: object())
    monkeypatch.setattr(next_card_priority, 'configure_next_card_work', lambda **kwargs: registration.update(kwargs))
    monkeypatch.setattr(game_feedback, 'effect_id', lambda _key: 'retained')
    next_card_priority.register_current_next_card_priority(read=read, clock=lambda: NOW,
        local_handoff_prepare=lambda **kwargs: selected.append(kwargs.get('before_priority')))
    prepared = registration['prepare']()
    expected = priority('2026-10-29T11:00:00Z', 'reconciliation', 'retained')
    assert prepared.priority == expected and selected == [expected]
