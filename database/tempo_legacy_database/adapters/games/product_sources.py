"""Canonical Games publication enlisted in the existing product page commit.

PGN work finishes before the short writer transaction. Existing Games SQL helpers
own library mutations; no analysis/derivation producer or Study writer is called.
"""
from contextlib import contextmanager
from datetime import datetime
import json

from tempo_game_evidence_contracts.legacy import fingerprint

from app.games.feedback.ports import ConcurrentUpdate
from app.games.ingestion.normalizer import normalize_provider_pgn
from ..postgres_store import PostgresConnection
from .product_feedback import PostgresGamesStore
from .sync import records as storage
from .authority.headers import _GAME_COLUMNS as SOURCE_COLUMNS


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


def _current(database, record, *, for_update=False):
    aliases = _aliases("chesscom" if record.provider == "chess.com" else record.provider)
    rows = database.execute("""SELECT * FROM imported_games WHERE provider=ANY(%s)
        AND (provider_game_id=%s OR (provider_game_id IS NULL
            AND lower(username)=lower(%s) AND content_hash=%s)) LIMIT 2"""
        + (" FOR UPDATE" if for_update else ""),
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


def _source_header(record, existing):
    """Use the retained capture's exact projection and original stored spelling.

    Equivalent UTC spellings/JSON whitespace do not rewrite frozen evidence.
    Different history, player, result or occurrence is an unresolved correction,
    never permission to replace the source under an existing owner effect.
    """
    if existing is not None:
        if (existing["username"].casefold() != record.username.casefold()
                or (existing["color"], existing["result"], existing["start_fen"],
                    json.loads(existing["moves_json"])) != (
                    record.color, record.result, record.start_fen, record.uci_moves)
                or datetime.fromisoformat(existing["played_at"].replace("Z", "+00:00")) !=
                    datetime.fromisoformat(record.played_at.replace("Z", "+00:00"))):
            raise ValueError("canonical_imported_source_conflict")
        return {column: existing[column] for column in SOURCE_COLUMNS.split(",")}
    values = dict(zip(storage.GAME_COLUMNS, _values(record)))
    values.update(id=f"{record.provider}:{record.provider_game_id}",
                  provider=record.provider, adaptive_excluded=0)
    return {column: values[column] for column in SOURCE_COLUMNS.split(",")}


def _bound_game(game, header):
    return game.model_copy(update=dict(game_id=header["id"], game_revision=1,
        source_id=header["provider"], source_revision=1, source_digest=fingerprint(header),
        played_at=header["played_at"]), deep=True)


class PostgresCanonicalGamesStore(PostgresGamesStore):
    """Keep library identity, original source, deliveries and cursor atomic.

    Binding verified PGN to a canonical source is read-only. A concurrent import
    or source change rolls back the page for recomputation with the winning row.
    Original captures and the coordinator's correction quarantine stay intact.
    """
    def canonical_game_id(self, record, spec):
        prepared = _prepare(record, spec)
        with self._transaction() as database:
            existing = _current(database, prepared)
        return existing["id"] if existing else f"{prepared.provider}:{prepared.provider_game_id}"

    def bind_source(self, game, *, record, spec):
        """SourceIdentity port: preserve PGN proof separately from owner identity."""
        prepared = _prepare(record, spec)
        if (prepared.start_fen, prepared.uci_moves, prepared.color) != (
                game.starting_fen, list(game.moves), game.learner_color):
            raise ValueError("canonical_library_history_mismatch")
        with self._transaction() as database:
            header = _source_header(prepared, _current(database, prepared))
        return _bound_game(game, header)

    def commit_page(self, commit):
        # Provider subtype provenance is retained by the capture, even when the
        # shared SourceState serializes only the shared original record fields.
        captures = {capture.source_key: capture for capture in commit.captures}
        prepared = []
        for source in commit.sources:
            if source.normalized is None or source.quarantined:
                continue
            capture = captures.get(source.source_key)
            if capture is None:
                raise ValueError("canonical_publication_requires_original_capture")
            record = _prepare(capture.record, commit.job.spec,
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
                existing = _current(raw, record, for_update=True)
                try:
                    header = _source_header(record, existing)
                except ValueError:
                    raise ConcurrentUpdate("canonical_game_source_changed") from None
                if _bound_game(source.normalized, header) != source.normalized:
                    raise ConcurrentUpdate("canonical_game_source_changed")
                game_id = header["id"]
                # Enrich non-source metadata through the existing writer while
                # retaining every original authority field byte-for-byte.
                values = tuple(header.get(column, value)
                    for column, value in zip(storage.GAME_COLUMNS, _values(record)))
                if existing is None:
                    storage.insert_imported_game(database, game_id, record.provider, values)
                elif any(existing[column] != value for column, value in zip(storage.GAME_COLUMNS, values)):
                    storage.update_imported_game(database, game_id, values)

            @contextmanager
            def borrowed():
                yield raw
            PostgresGamesStore(borrowed).commit_page(commit)
