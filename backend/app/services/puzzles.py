from dataclasses import dataclass

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
