from datetime import date, datetime, timedelta, timezone


PGN = b'[Event "Durability"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *'


def test_default_scheduler_uses_local_day_not_utc_day(monkeypatch):
    import app.services.scheduler as scheduler

    class LocalDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 16)

    class UtcClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(scheduler, "date", LocalDate)
    monkeypatch.setattr(scheduler, "datetime", UtcClock)
    assert scheduler.schedule_review("again").due_date == date(2026, 9, 16)


def test_tomorrow_is_the_local_review_day_even_after_utc_midnight():
    from app.services.scheduler import schedule_review

    utc_now = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    local_day = date(2026, 9, 16)
    first = schedule_review("correct", reviewed_at=utc_now, review_day=local_day)
    assert first.due_date == local_day and first.requeue_today
    second = schedule_review(
        "correct",
        fsrs_card_json=first.fsrs_card_json,
        first_correct_at=first.first_correct_at,
        reinforcement_pending=True,
        reviewed_at=utc_now + timedelta(minutes=5),
        review_day=local_day,
    )
    assert second.due_date == date(2026, 9, 17) and second.interval_days == 1
