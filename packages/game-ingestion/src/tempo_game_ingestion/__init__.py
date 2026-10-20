"""Standalone game ingestion: injected bytes in, canonical game value out."""
from dataclasses import dataclass
from pathlib import Path

from tempo_game_review_contracts import CanonicalGame, GameInput
from .pgn import MAX_SOURCE_BYTES, normalize_pgn


@dataclass(frozen=True)
class BytesInput:
    data: bytes

    def read(self) -> bytes:
        return self.data


@dataclass(frozen=True)
class FileInput:
    path: Path

    def read(self) -> bytes:
        # Bound file reads before allocation; injected transports own their read limit.
        with self.path.open("rb") as stream:
            return stream.read(MAX_SOURCE_BYTES + 1)


class PgnGameIngester:
    """Consume exactly one explicit source, without storage or downstream effects."""

    def ingest(self, source: GameInput, *, workspace_id: str, game_id: str,
               game_revision: int, source_id: str, source_revision: int,
               player: str, played_at: str | None = None) -> CanonicalGame:
        game = normalize_pgn(source.read(), player, played_at=played_at)
        return CanonicalGame(
            workspace_id=workspace_id, game_id=game_id, game_revision=game_revision,
            source_id=source_id, source_revision=source_revision,
            source_digest=game["source_digest"], starting_fen=game["starting_fen"],
            moves=game["moves"], learner_color=game["observed_color"],
            played_at=game["played_at"],
        )


__all__ = ["PgnGameIngester", "BytesInput", "FileInput", "normalize_pgn", "MAX_SOURCE_BYTES"]
