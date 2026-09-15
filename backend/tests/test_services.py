import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.services.analysis import MAIA_VERSION, STOCKFISH_VERSION, explorer_url
from app.services.cards import card_id
from app.services.puzzles import load_packaged_decks, validate_puzzle_record
from app.services.pgn import parse_pgn, prefix_through_user_moves
from app.services.scheduler import schedule_review, unlock_ready
from app.services.endgames import generate_position, normalized_material
from app.services.game_analysis import classify_swings
import chess


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


class PackagedContentTests(unittest.TestCase):
    def test_pgn_import_collects_main_lines_and_variations(self) -> None:
        games, lines = parse_pgn('[Event "Imported"]\n\n1. e4 (1. d4 d5) e5 2. Nf3 *')
        self.assertEqual(games, 1)
        self.assertEqual({tuple(line.moves) for line in lines}, {("e2e4", "e7e5", "g1f3"), ("d2d4", "d7d5")})

    def test_pgn_import_reads_comments_arrows_and_squares(self) -> None:
        _, lines = parse_pgn('[Event "Notes"]\n\n1. e4 {Keep the center. [%cal Ge2e4,Rd8h4] [%csl Ye5]} e5 *')
        annotation = lines[0].annotations[0]
        self.assertEqual(annotation.comment, "Keep the center.")
        self.assertEqual(annotation.arrows[0], {"from": "e2", "to": "e4", "color": "green"})
        self.assertEqual(annotation.squares[0], {"square": "e5", "color": "yellow"})

    def test_all_tactic_decks_have_the_requested_sizes_and_unique_ids(self) -> None:
        path = Path(__file__).parents[2] / "public" / "data" / "tactics-decks.json"
        decks = load_packaged_decks(path)
        self.assertEqual(len(decks), 52)
        self.assertTrue(all(len(cards) == (250 if deck_id.endswith("-focused") else 100) for deck_id, cards in decks.items()))
        puzzle_ids = [str(card["PuzzleId"]) for cards in decks.values() for card in cards]
        self.assertEqual(len(puzzle_ids), len(set(puzzle_ids)))

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

    def test_00shx_canonical_training_position_keeps_both_bishops(self) -> None:
        fen, solution = validate_puzzle_record({"FEN":"q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17","Moves":"e8d7 a2e6 d7d8 f7f8"})
        board = chess.Board(fen)
        self.assertEqual(board.piece_at(chess.A2), chess.Piece(chess.BISHOP, chess.WHITE))
        self.assertEqual(board.piece_at(chess.A3), chess.Piece(chess.BISHOP, chess.WHITE))
        self.assertEqual(solution, ["a2e6", "d7d8", "f7f8"])

    def test_endgame_generator_is_legal_and_seven_piece_bounded(self) -> None:
        for seed in range(40):
            board = chess.Board(generate_position("KQR", "K", "white", seed=seed))
            self.assertTrue(board.is_valid())
            idle_king = board.king(not board.turn)
            self.assertIsNotNone(idle_king)
            self.assertFalse(board.is_attacked_by(board.turn, idle_king))
        self.assertEqual(normalized_material("Q & K"), "KQ")
        with self.assertRaises(ValueError):
            generate_position("KPPPPPP", "KPPPPP", "white")

    def test_game_analysis_finds_mistake_and_missed_punishment(self) -> None:
        result = classify_swings([{"ply":0,"before_cp":150,"after_cp":20,"opponent_created_chance":True}], "white", 100)
        self.assertEqual(result["major_mistake_ply"], 0)
        self.assertEqual(result["missed_punishment_ply"], 0)


if __name__ == "__main__":
    unittest.main()
