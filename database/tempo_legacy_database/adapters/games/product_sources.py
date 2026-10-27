"""Canonical Games publication enlisted in the existing product page commit.

PGN work finishes before the short writer transaction. Existing Games SQL helpers
own library mutations; no analysis/derivation producer or Study writer is called.
"""
from contextlib import contextmanager
from datetime import datetime
import json

from app.games.feedback.ports import ConcurrentUpdate
from app.games.ingestion.normalizer import normalize_provider_pgn
from ..postgres_store import PostgresConnection
from .product_feedback import PostgresGamesStore
from .sync import records as storage


def _aliases(provider):
    return ("chess.com", "chesscom") if provider == "chesscom" else ("lichess",)


def _prepare(record, spec, provider_document=None):
    document = provider_document or record.model_dump(mode="json")
    pairs = document.get("source_metadata", ())
    if len({pair[0] for pair in pairs}) != len(pairs):
        raise ValueError("duplicate_provider_metadata")
    metadata = {key: json.loads(value) for key, value in pairs}
    speed = metadata.get("speed", metadata.get("time_class"))
    rated = metadata.get("rated")
    if speed not in {"bullet", "blitz", "rapid", "classical"} or type(rated) is not bool:
        raise ValueError("explicit_provider_speed_and_rated_facts_required")
    if record.played_at is None:
        raise ValueError("canonical_library_occurrence_time_unavailable")
    # Reuse the retained importer's datetime representation for its old content
    # hash (microseconds when present). The original provider timestamp/precision
    # stays untouched in the product capture and canonical comparison record.
    legacy_time = datetime.fromisoformat(record.played_at.replace("Z", "+00:00")).isoformat()
    return normalize_provider_pgn(_aliases(spec.provider)[0], spec.account, record.pgn,
        provider_game_id=record.source_game_id, played_at=legacy_time,
        speed=speed, rated=rated,
        game_url=metadata.get("game_url", metadata.get("url")))


def _current(database, record):
    aliases = _aliases("chesscom" if record.provider == "chess.com" else record.provider)
    rows = database.execute("""SELECT * FROM imported_games WHERE provider=ANY(%s)
        AND (provider_game_id=%s OR (provider_game_id IS NULL
            AND lower(username)=lower(%s) AND content_hash=%s)) LIMIT 2""",
        (list(aliases), record.provider_game_id, record.username, record.content_hash)).fetchall()
    if len(rows) > 1:
        raise ValueError("ambiguous_canonical_game_identity")
    return rows[0] if rows else None


def _values(record):
    return (record.provider_game_id, record.content_hash, record.username,
        record.played_at, record.speed, int(record.rated), record.color,
        record.result, record.start_fen, json.dumps(record.uci_moves),
        record.game_url, record.opening_name, record.player_rating,
        record.opponent_rating, record.rating_change, record.time_control)


class PostgresCanonicalGamesStore(PostgresGamesStore):
    """Keep library identity, original source, deliveries and cursor atomic.

    Resolving an ID is read-only. A concurrent legacy import/alias upgrade causes
    the whole page to roll back and be recomputed with the winning canonical ID.
    Original captures and the coordinator's correction quarantine stay intact.
    """
    def canonical_game_id(self, record, spec):
        prepared = _prepare(record, spec)
        with self._transaction() as database:
            existing = _current(database, prepared)
        return existing["id"] if existing else f"{prepared.provider}:{prepared.provider_game_id}"

    def commit_page(self, commit):
        # Provider subtype provenance is retained by the capture, even when the
        # shared SourceState serializes only the shared original record fields.
        captures = {(capture.source_key, capture.record.source_revision,
                     capture.record.source_digest): capture for capture in commit.captures}
        prepared = []
        for source in commit.sources:
            if source.normalized is None or source.quarantined:
                continue
            capture = captures.get((source.source_key, source.original.source_revision,
                                    source.original.source_digest))
            if capture is None:
                raise ValueError("canonical_publication_requires_original_capture")
            record = _prepare(source.original, commit.job.spec,
                              json.loads(capture.provider_record_json))
            game = source.normalized
            if (record.start_fen, record.uci_moves, record.color) != (
                    game.starting_fen, list(game.moves), game.learner_color):
                raise ValueError("canonical_library_history_mismatch")
            prepared.append((source, record))
        with self._transaction() as raw:
            database = PostgresConnection(raw)
            for source, record in sorted(prepared, key=lambda item: item[0].source_key):
                for alias in sorted(_aliases(commit.job.spec.provider)):
                    storage.lock_import_identity(database, f"{alias}:{record.provider_game_id}")
                existing = _current(raw, record)
                game_id = existing["id"] if existing else f"{record.provider}:{record.provider_game_id}"
                if game_id != source.normalized.game_id:
                    raise ConcurrentUpdate("canonical_game_identity_changed")
                values = _values(record)
                if existing is None:
                    storage.insert_imported_game(database, game_id, record.provider, values)
                elif any(existing[column] != value for column, value in zip(storage.GAME_COLUMNS, values)):
                    storage.update_imported_game(database, game_id, values)

            @contextmanager
            def borrowed():
                yield raw
            PostgresGamesStore(borrowed).commit_page(commit)
