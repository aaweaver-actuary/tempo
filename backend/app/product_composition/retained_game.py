"""Pure canonical projection shared by intake and independent maintenance."""
import json

from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_game_review_contracts import CanonicalGame


def retained_canonical_game(capture) -> CanonicalGame:
    """Retain the import's provider namespace and exact source fingerprint."""
    raw = capture["game"]
    return CanonicalGame(workspace_id=capture["authority"]["workspace_id"],
        game_id=raw["id"], game_revision=1, source_id=raw["provider"],
        source_revision=1, source_digest=fingerprint(raw), starting_fen=raw["start_fen"],
        moves=json.loads(raw["moves_json"]), learner_color=raw["color"], played_at=raw["played_at"])
