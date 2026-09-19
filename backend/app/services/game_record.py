"""Validated intermediate game representation before database persistence."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GameRecord:
    provider: str
    username: str
    provider_game_id: str
    played_at: str
    speed: str
    rated: bool
    color: str
    result: str
    start_fen: str
    uci_moves: list[str] = field(default_factory=list)
    opening_name: str = ""
    game_url: str = ""
    content_hash: str = ""
    player_rating: int | None = None
    opponent_rating: int | None = None
    rating_change: int | None = None
    time_control: str = ""
