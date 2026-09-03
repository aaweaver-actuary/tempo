import unittest
from datetime import date

from app.services.cards import card_id
from app.services.scheduler import schedule_review


class CardIdentityTests(unittest.TestCase):
    def test_normalizes_whitespace(self) -> None:
        first = card_id("start  fen", ["e4", " c5 "])
        second = card_id("start fen", ["e4", "c5"])
        self.assertEqual(first, second)

    def test_moves_are_part_of_identity(self) -> None:
        self.assertNotEqual(card_id("start", ["e4"]), card_id("start", ["d4"]))


class SchedulerTests(unittest.TestCase):
    def test_again_returns_to_a_future_daily_queue(self) -> None:
        result = schedule_review("again", interval_days=12, ease=2.5, repetitions=4, lapses=0)
        self.assertEqual(result.interval_days, 1)
        self.assertGreater(result.due_date, date.today())
        self.assertEqual(result.lapses, 1)

    def test_four_successful_repetitions_can_mature_a_card(self) -> None:
        result = schedule_review("good", interval_days=10, ease=2.5, repetitions=3, lapses=0)
        self.assertEqual(result.state, "mature")


if __name__ == "__main__":
    unittest.main()
