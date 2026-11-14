"""Bounded review handoff contracts; native locking is proved by the Docker suite."""

from datetime import date


def test_legacy_review_payload_omits_absent_prepared_identity_and_keeps_request_hash():
    from app.command_gateway import request_digest
    from app.contracts.transport.study import ReviewRequest

    legacy = {
        "outcome": "correct",
        "guided": False,
        "queue_entry_id": 12,
        "recorded_at": None,
        "expected_review_id": None,
        "expected_revision": 1,
        "attempt_id": "legacy",
    }
    request = ReviewRequest.model_validate(legacy)
    assert request.durable_payload() == legacy
    assert request_digest(
        "cards.review", {"card_id": "a", "review": request.durable_payload()}
    ) == request_digest("cards.review", {"card_id": "a", "review": legacy})


def test_prepared_control_precedes_pending_study_work_without_applying_during_preparation(
    monkeypatch,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.study.interfaces.next_card import NoNextCardWork

    from app.product_composition import next_card_priority as composition
    from app.study import game_feedback

    prepared, study_preparations = {}, []

    def no_apply(_writer):
        raise AssertionError(
            "Preparation applied a control before the operation receipt fence"
        )

    control = SimpleNamespace(priority=None, is_control=True, apply=no_apply)
    observed = SimpleNamespace(
        key=object(),
        qualification=None,
        progress=SimpleNamespace(
            determined_at="2026-01-01T00:00:00Z", stream="forward"
        ),
    )

    @contextmanager
    def read():
        yield object()

    monkeypatch.setattr(
        composition, "configure_next_card_work", lambda **work: prepared.update(work)
    )
    monkeypatch.setattr(composition, "installed", lambda _db: True)
    monkeypatch.setattr(composition, "feedback_preparation_admitted", lambda _db: True)
    monkeypatch.setattr(
        composition, "next_feedback_preparation", lambda *_args, **_kwargs: observed
    )
    monkeypatch.setattr(
        composition,
        "PostgresGameFeedbackStore",
        lambda _db: SimpleNamespace(study_day=lambda _now: date(2026, 1, 1)),
    )
    monkeypatch.setattr(game_feedback, "effect_id", lambda _key: "accepted-study")
    monkeypatch.setattr(
        composition,
        "prepare_feedback_effect",
        lambda *_args, **_kwargs: study_preparations.append(True) or object(),
    )
    composition.register_current_next_card_priority(
        read=read, local_handoff_prepare=lambda **_kwargs: control
    )
    assert prepared["prepare"]() is control
    assert study_preparations == []
    assert NoNextCardWork().is_control is False


def test_optional_preparation_exception_does_not_hide_accepted_study_work(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.product_composition import next_card_priority as composition
    from app.study import game_feedback

    prepared, applied = {}, []
    observed = SimpleNamespace(
        key=object(),
        qualification=None,
        progress=SimpleNamespace(
            determined_at="2026-01-01T00:00:00Z", stream="forward"
        ),
    )

    @contextmanager
    def read():
        yield object()

    def optional(**_kwargs):
        raise ImportError("optional Games module absent")

    monkeypatch.setattr(
        composition, "configure_next_card_work", lambda **work: prepared.update(work)
    )
    monkeypatch.setattr(composition, "installed", lambda _db: True)
    monkeypatch.setattr(composition, "feedback_preparation_admitted", lambda _db: True)
    monkeypatch.setattr(
        composition, "next_feedback_preparation", lambda *_args, **_kwargs: observed
    )
    monkeypatch.setattr(
        composition,
        "PostgresGameFeedbackStore",
        lambda _db: SimpleNamespace(study_day=lambda _now: date(2026, 1, 1)),
    )
    monkeypatch.setattr(game_feedback, "effect_id", lambda _key: "accepted-study")
    monkeypatch.setattr(
        composition,
        "prepare_feedback_effect",
        lambda *args, **kwargs: applied.append(args[0]) or object(),
    )
    composition.register_current_next_card_priority(
        read=read, local_handoff_prepare=optional
    )
    work = prepared["prepare"]()
    assert isinstance(work, composition.PreparedCurrentStudyWork) and applied == [
        observed
    ]
