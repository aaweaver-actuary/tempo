import unittest
from datetime import date, datetime, timedelta, timezone
from tempo_scheduling import schedule_review, unlock_ready

class SchedulerTests(unittest.TestCase):
    def test_again_is_reshuffled_into_todays_queue(self) -> None:
        result = schedule_review("again", interval_days=12)
        self.assertEqual(result.interval_days, 0)
        self.assertEqual(result.due_date, date.today())
        self.assertTrue(result.requeue_today)
        self.assertEqual(result.requeue_after_cards, 4)

    def test_unlock_requires_stability_across_days(self) -> None:
        self.assertTrue(unlock_ready(14, 3, ["correct", "correct"]))
        self.assertFalse(unlock_ready(13, 3, ["correct", "correct"]))
        self.assertFalse(unlock_ready(14, 2, ["correct", "correct"]))
        self.assertFalse(unlock_ready(14, 3, ["correct", "again"]))

    def test_first_clean_pass_returns_to_end_then_fsrs_schedules(self) -> None:
        first = schedule_review("correct")
        self.assertTrue(first.requeue_today)
        self.assertIsNone(first.requeue_after_cards)
        second = schedule_review("correct", fsrs_card_json=first.fsrs_card_json, first_correct_at=first.first_correct_at, reinforcement_pending=True)
        self.assertFalse(second.requeue_today)
        self.assertGreaterEqual(second.interval_days, 1)
        self.assertLessEqual(second.interval_days, 365)

    def test_overdue_growth_is_capped(self) -> None:
        first = schedule_review("correct")
        second = schedule_review("correct", fsrs_card_json=first.fsrs_card_json, first_correct_at=first.first_correct_at, reinforcement_pending=True)
        much_later = datetime.now(timezone.utc) + timedelta(days=180)
        third = schedule_review("correct", interval_days=max(1, second.interval_days), fsrs_card_json=second.fsrs_card_json, first_correct_at=first.first_correct_at, reviewed_at=much_later)
        self.assertLessEqual(third.interval_days, max(2, round(max(1, second.interval_days) * 2.5)))
        self.assertLessEqual(third.interval_days, 365)

    def test_hard_track_enters_and_recovers_on_fixed_cadence(self) -> None:
        entered = schedule_review("again", recent_attempts=["again", "correct", "again", "correct"])
        self.assertEqual(entered.scheduling_mode, "hard")
        self.assertTrue(entered.suggest_shorter_prefix)
        first = schedule_review("correct", fsrs_card_json=entered.fsrs_card_json, first_correct_at="seen", scheduling_mode="hard", recent_attempts=list(entered.recent_attempts))
        self.assertEqual(first.interval_days, 1)
        second = schedule_review("correct", fsrs_card_json=first.fsrs_card_json, first_correct_at="seen", scheduling_mode="hard", hard_correct_streak=1, recent_attempts=list(first.recent_attempts))
        self.assertEqual(second.interval_days, 3)
        third = schedule_review("correct", fsrs_card_json=second.fsrs_card_json, first_correct_at="seen", scheduling_mode="hard", hard_correct_streak=2, recent_attempts=list(second.recent_attempts))
        self.assertEqual(third.scheduling_mode, "normal")




def test_fixed_time_schedule_matches_golden_output():
    from dataclasses import asdict
    import json
    from pathlib import Path
    from fsrs import Card
    reviewed_at = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    outcomes = []
    for outcome in ("again", "correct"):
        result = asdict(schedule_review(outcome, fsrs_card_json=Card(card_id=1, due=reviewed_at).to_json(), reviewed_at=reviewed_at, review_day=reviewed_at.date()))
        result["due_date"] = result["due_date"].isoformat()
        result["recent_attempts"] = list(result["recent_attempts"])
        outcomes.append(result)
    assert outcomes == json.loads((Path(__file__).parent / "golden.json").read_text())


def test_installed_scheduling_exports_and_identity():
    import tempo_scheduling
    import sys
    assert tempo_scheduling.__all__ == ["Schedule", "schedule_review", "unlock_ready"]
    assert not hasattr(tempo_scheduling, "Scheduler")
    assert "app" not in sys.modules
    assert ".venv" in tempo_scheduling.__file__ or "site-packages" in tempo_scheduling.__file__


def test_invalid_outcome_is_rejected():
    with __import__("pytest").raises(ValueError, match="outcome must"):
        schedule_review("unknown")
