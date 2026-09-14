import unittest
from datetime import date
from pathlib import Path

from app.services.analysis import MAIA_VERSION, STOCKFISH_VERSION, explorer_url
from app.services.cards import card_id
from app.services.puzzles import load_packaged_decks
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


class PackagedContentTests(unittest.TestCase):
    def test_twelve_decks_have_one_hundred_cards_each(self) -> None:
        path = Path(__file__).parents[2] / "public" / "data" / "tactics-decks.json"
        decks = load_packaged_decks(path)
        self.assertEqual(len(decks), 12)
        self.assertTrue(all(len(cards) == 100 for cards in decks.values()))

    def test_analysis_integrations_are_current_and_fen_safe(self) -> None:
        self.assertEqual(STOCKFISH_VERSION, 19)
        self.assertEqual(MAIA_VERSION, 3)
        url = explorer_url("8/8/8/8/8/8/8/K6k w - - 0 1")
        self.assertIn("explorer.lichess.org/lichess", url)
        self.assertIn("fen=8%2F8%2F8", url)


if __name__ == "__main__":
    unittest.main()
