"""Stable source-game deduplication, without population probability fusion."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

import chess
from tempo_evidence_math import opening_position_key

from app.contracts.product_v02.analysis import RankingCandidate
from app.contracts.product_v02.repertoire import DecisionPresentation

IMPORTANCE_VERSION = "current-decision-personal-importance-v1"
SourceGame = tuple[str, str, str, int]


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
                      match_move: bool = True) -> tuple[SourceGame, ...]:
    """Own decisions were encountered even when the learner played another move."""
    return tuple((*game.source_id, game.source_revision) for game in deduplicate_games(games)
                 if any((item.repertoire_id, item.position_key) == (target.repertoire_id, target.position_key)
                        and (not match_move or item.move_uci == target.move_uci) for item in game.encounters))


@dataclass(frozen=True)
class DecisionImportance:
    """Calculation detail, not an admission command or lapse eligibility proof."""
    decision: DecisionPresentation
    facts: RankingCandidate
    encounter_sources: tuple[SourceGame, ...]
    differing_response_sources: tuple[SourceGame, ...]
    dependency_digest: str
    policy_version: str = IMPORTANCE_VERSION


def decision_importance(*, current_approved_decisions: tuple[DecisionPresentation, ...],
                        games: tuple[PersonalGame, ...]) -> tuple[DecisionImportance, ...]:
    """Project unique source-game importance for either current introduction state.

    The caller supplies exact owner-mapped approved decisions and the existing
    comparison's repertoire-scoped observations, never arbitrary FEN matches.
    Study must revalidate that authority when consuming the factual result.
    Differing play concerns the supplied current response, not historical recall.
    """
    decisions = {}
    positions = {}
    for decision in current_approved_decisions:
        identity = decision.repertoire_id, decision.decision_id
        position = decision.repertoire_id, decision.position_key
        if identity in decisions and decisions[identity] != decision:
            raise ValueError("ambiguous_current_decision_mapping")
        if position in positions and positions[position] != decision.selected_response_uci:
            raise ValueError("conflicting_current_approved_responses")
        positions[position] = decision.selected_response_uci
        decisions[identity] = decision
    observations = deduplicate_games(games)
    results = []
    for identity in sorted(decisions):
        decision = decisions[identity]
        if not decision.available or decision.introduction not in {"introduced", "unintroduced"}:
            continue
        target = PersonalEncounter(decision.repertoire_id, decision.position_key, decision.selected_response_uci)
        sources = encounter_sources(observations, target, match_move=False)
        differing = tuple((*game.source_id, game.source_revision) for game in observations
            if any(item.repertoire_id == target.repertoire_id and item.position_key == target.position_key
                   and item.move_uci != target.move_uci for item in game.encounters))
        facts = RankingCandidate(decision_id=decision.decision_id, repertoire_id=decision.repertoire_id,
            reach_probability=None, evidence_support=None, uncertainty=None,
            personal_encounters=len(sources))
        digest = hashlib.sha256(json.dumps(dict(policy_version=IMPORTANCE_VERSION,
            decision=decision.model_dump(mode="json"), encounters=sources, differing=differing),
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        results.append(DecisionImportance(decision, facts, sources, differing, digest))
    return tuple(results)
