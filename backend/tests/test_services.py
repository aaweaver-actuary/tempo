import unittest
from datetime import date

from app.services.cards import card_id
from app.services.scheduler import schedule_review, unlock_ready


class CardIdentityTests(unittest.TestCase):
    def test_normalizes_whitespace(self) -> None:
        first = card_id("start  fen", ["e4", " c5 "])
        second = card_id("start fen", ["e4", "c5"])
        self.assertEqual(first, second)

    def test_moves_are_part_of_identity(self) -> None:
        self.assertNotEqual(card_id("start", ["e4"]), card_id("start", ["d4"]))

    def test_fen_clocks_do_not_split_the_same_position(self) -> None:
        first = card_id("8/8/8/8/8/8/8/K6k w - - 0 1", ["a1a2"])
        second = card_id("8/8/8/8/8/8/8/K6k w - - 17 42", ["a1a2"])
        self.assertEqual(first, second)


class SchedulerTests(unittest.TestCase):
    def test_again_is_reshuffled_into_todays_queue(self) -> None:
        result = schedule_review("again", interval_days=12, ease=2.5, repetitions=4, lapses=0)
        self.assertEqual(result.interval_days, 0)
        self.assertEqual(result.due_date, date.today())
        self.assertEqual(result.lapses, 1)
        self.assertTrue(result.requeue_today)
        self.assertEqual(result.requeue_after_cards, 4)

    def test_unlock_requires_stability_across_days(self) -> None:
        self.assertTrue(unlock_ready(14, 3, ["good", "hard"]))
        self.assertFalse(unlock_ready(13, 3, ["good", "hard"]))
        self.assertFalse(unlock_ready(14, 2, ["good", "hard"]))
        self.assertFalse(unlock_ready(14, 3, ["good", "again"]))


if __name__ == "__main__":
    unittest.main()
