"""Stable source-game deduplication, without population probability fusion."""
from __future__ import annotations

from dataclasses import dataclass

import chess
from tempo_evidence_math import opening_position_key


@dataclass(frozen=True, order=True)
class PersonalEncounter:
    repertoire_id: str
    position_key: str
    move_uci: str

    def __post_init__(self):
        if not self.repertoire_id or opening_position_key(self.position_key) != self.position_key:
            raise ValueError("personal_encounter_requires_exact_repertoire_position")
        if chess.Move.from_uci(self.move_uci) not in chess.Board(self.position_key).legal_moves:
            raise ValueError("personal_encounter_requires_legal_move")


@dataclass(frozen=True)
class PersonalGame:
    provider: str
    account: str
    game_id: str
    source_revision: int
    encounters: tuple[PersonalEncounter, ...]

    def __post_init__(self):
        if not all((self.provider, self.account, self.game_id)):
            raise ValueError("personal_game_requires_stable_source_identity")
        if type(self.source_revision) is not int or self.source_revision < 1:
            raise ValueError("personal_game_requires_positive_source_revision")
        object.__setattr__(self, "encounters", tuple(sorted(set(self.encounters))))

    @property
    def source_id(self) -> tuple[str, str, str]:
        return self.provider, self.account, self.game_id


def deduplicate_games(games: tuple[PersonalGame, ...]) -> tuple[PersonalGame, ...]:
    """Latest supplied complete observation set wins; same-revision conflict fails."""
    revisions = {}
    latest = {}
    for game in games:
        key = game.source_id, game.source_revision
        if key in revisions and revisions[key] != game:
            raise ValueError("conflicting_personal_game_revision")
        revisions[key] = game
        if game.source_id not in latest or latest[game.source_id].source_revision < game.source_revision:
            latest[game.source_id] = game
    return tuple(latest[key] for key in sorted(latest))


def encounter_sources(games: tuple[PersonalGame, ...], target: PersonalEncounter, *,
                      match_move: bool = True) -> tuple[tuple[str, str, str, int], ...]:
    """Own decisions were encountered even when the learner played another move."""
    return tuple((*game.source_id, game.source_revision) for game in deduplicate_games(games)
                 if any((item.repertoire_id, item.position_key) == (target.repertoire_id, target.position_key)
                        and (not match_move or item.move_uci == target.move_uci) for item in game.encounters))
