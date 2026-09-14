from dataclasses import dataclass
import json
from pathlib import Path

import chess


@dataclass(frozen=True)
class LichessPuzzle:
    puzzle_id: str
    source_fen: str
    moves: list[str]
    rating: int
    popularity: int
    themes: tuple[str, ...]
    game_url: str

    def training_position(self) -> tuple[str, list[str]]:
        """Return the position after Lichess's setup move and the moves to test."""
        board = chess.Board(self.source_fen)
        board.push_uci(self.moves[0])
        return board.fen(), self.moves[1:]


def parse_lichess_puzzle_row(row: dict[str, str]) -> LichessPuzzle:
    moves = row["Moves"].split()
    if len(moves) < 2:
        raise ValueError("A Lichess puzzle needs a setup move and at least one solution move")
    return LichessPuzzle(
        puzzle_id=row["PuzzleId"],
        source_fen=row["FEN"],
        moves=moves,
        rating=int(row["Rating"]),
        popularity=int(row["Popularity"]),
        themes=tuple(filter(None, row.get("Themes", "").split())),
        game_url=row.get("GameUrl", ""),
    )


def load_packaged_decks(path: str | Path) -> dict[str, list[dict[str, object]]]:
    """Load the deterministic 100-card motif packs bundled with the local app."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    decks: dict[str, list[dict[str, object]]] = {}
    for record in records:
        deck_id = str(record["DeckId"])
        decks.setdefault(deck_id, []).append(record)
    if any(len(cards) != 100 for cards in decks.values()):
        raise ValueError("Every packaged tactics deck must contain exactly 100 cards")
    return decks


def validate_puzzle_record(record: dict[str, object]) -> tuple[str, list[str]]:
    """Validate the canonical Lichess setup move and complete solution."""
    source_fen = str(record["FEN"])
    raw_moves = record["Moves"]
    moves = raw_moves.split() if isinstance(raw_moves, str) else list(raw_moves)
    if len(moves) < 2:
        raise ValueError("Puzzle needs a setup move and solution")
    board = chess.Board(source_fen)
    for value in moves:
        move = chess.Move.from_uci(str(value))
        if move not in board.legal_moves:
            raise ValueError(f"Illegal puzzle move {value}")
        board.push(move)
        if value == moves[0]:
            training_fen = board.fen()
    return training_fen, [str(move) for move in moves[1:]]
