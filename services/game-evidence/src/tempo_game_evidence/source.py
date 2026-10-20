"""Compatibility facade for extracted ingestion and shared legal replay owners."""
from datetime import datetime, timezone
import hashlib
import json

from tempo_game_ingestion import MAX_SOURCE_BYTES, normalize_pgn
from tempo_game_review_contracts import replay_line

INDEX_VERSION = 1


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc)


def replay_positions(game):
    """Retain Game Evidence's existing dictionary API without forking replay."""
    return replay_line(game["starting_fen"], game["moves"])
