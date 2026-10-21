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


def prepare_import_identity(workspace, provider, source_id, player, raw, *, played_at=None):
    """Cheap source validation/identity before the unchanged replay shortcut."""
    if len(raw)>MAX_SOURCE_BYTES:
        raise ValueError("source_byte_bound_exceeded")
    if not all(isinstance(value, str) and value.strip() for value in (workspace, provider, source_id, player)):
        raise ValueError("explicit_source_identity_required")
    game_id = identity(workspace, provider, source_id, player.casefold())
    raw_digest = hashlib.sha256(raw).hexdigest()
    input_digest = identity(raw_digest, str(played_at) if played_at else None)
    return game_id, raw_digest, input_digest


def prepare_import_document(workspace, provider, source_id, player, raw, raw_digest, game_id, *, played_at=None):
    """Normalize only after replay has been ruled out; retain error documents."""
    try:
        document = normalize_pgn(raw, player, played_at=played_at)
    except (ValueError, UnicodeError) as error:
        document = {"source_error": str(error) if isinstance(error, ValueError) else "invalid_utf8",
                    "player": player.casefold(), "played_at": None}
    document.update(workspace_id=workspace, game_id=game_id, provider=provider,
                    provider_game_id=source_id, source_digest=raw_digest)
    return document


def stamp_import_document(document, revision):
    document["revision"] = revision
    document["imported_at"] = utc_now().isoformat()


def normalized_import_player(player):
    return player.casefold()
