"""Wire retained owner captures to the existing Games command projection."""
from dataclasses import dataclass
import json

from app.contracts.product_v02.games import Finding
from app.contracts.product_v02.learning import GameEncounter
from app.contracts.product_v02.repertoire import IntroductionGameScope, RepertoireSnapshot
from app.games.feedback.comparison import compare_game
from app.games.feedback.coordinator import project_encounters
from app.repertoire.interfaces import build_index, project_capture

from ..feedback_commands import retained_time_precision
from ..retained_game import retained_canonical_game


@dataclass(frozen=True)
class PreparedEncounterCapture:
    """Local assembly facts, retaining the existing shared values unchanged."""
    capture: dict
    snapshot: RepertoireSnapshot
    findings: tuple[Finding, ...]
    encounters: tuple[GameEncounter, ...]


def prepare_captured_encounters(capture, *, detected_at):
    """No I/O or effect: preserve canonical identity without reconstructing PGN.

    The bounded history caller owns selection, admission and durable progress.
    The same published projection used by capture_page owns shared commands.
    """
    capture = json.loads(json.dumps(capture))
    raw = capture["game"]
    provider = {"chess.com": "chesscom"}.get(raw["provider"], raw["provider"])
    game = retained_canonical_game(capture)
    snapshot = project_capture(capture, comparison_game=game)
    findings = compare_game(game, provider=provider, time_precision=retained_time_precision(raw),
        snapshot=snapshot, reentry_lookup=build_index(snapshot))
    encounters = project_encounters(game, findings=findings, snapshots=(snapshot,),
        source=IntroductionGameScope(provider=provider, game_id=game.game_id,
            source_revision=game.source_revision, source_digest=game.source_digest),
        detected_at=detected_at, occurred_at=game.played_at)
    return PreparedEncounterCapture(capture, snapshot, findings, encounters)
