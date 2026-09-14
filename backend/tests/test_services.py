import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.services.analysis import MAIA_VERSION, STOCKFISH_VERSION, explorer_url
from app.services.cards import card_id
from app.services.puzzles import load_packaged_decks
from app.services.pgn import prefix_through_user_moves
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

    def test_six_move_prefix_ends_on_trained_side(self) -> None:
        moves = ["e2e4","e7e5","g1f3","b8c6","f1b5","a7a6","b5a4","g8f6","e1g1","f8e7","f1e1","b7b5","a4b3"]
        white = prefix_through_user_moves("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves, "white", 6)
        black = prefix_through_user_moves("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves, "black", 6)
        self.assertEqual(white[-1], "f1e1")
        self.assertEqual(black[-1], "b7b5")


if __name__ == "__main__":
    unittest.main()
