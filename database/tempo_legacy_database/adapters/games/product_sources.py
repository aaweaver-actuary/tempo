"""Canonical Games publication enlisted in the existing product page commit.

PGN work finishes before the short writer transaction. Existing Games SQL helpers
own library mutations; no analysis/derivation producer or Study writer is called.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import hashlib
import json

import chess.pgn

from tempo_game_evidence_contracts.legacy import fingerprint

from app.games.feedback.ports import Capture, ConcurrentUpdate, JobState, SourceState, stable_id
from app.games.feedback.review import SyncConfiguration
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


def legacy_lichess_precision_matches(record, stored_time):
    """Prove an old PGN UTC-second represents this exact provider instant.

    The legacy Lichess importer used UTCDate/UTCTime headers, which have second
    precision. Never infer compatibility from proximity alone.
    """
    if record.time_precision != "millisecond" or not record.played_at:
        return False
    try:
        headers = chess.pgn.read_headers(io.StringIO(record.pgn))
        if headers is None or not headers.get("UTCDate") or not headers.get("UTCTime"):
            return False
        header = datetime.strptime(headers["UTCDate"] + " " + headers["UTCTime"], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
        stored = datetime.fromisoformat(stored_time.replace("Z", "+00:00"))
        precise = datetime.fromisoformat(record.played_at.replace("Z", "+00:00"))
        if stored.utcoffset() is None or precise.utcoffset() is None:
            return False
        return (stored.astimezone(timezone.utc) == header
            and precise.astimezone(timezone.utc).replace(microsecond=0) == header)
    except (ValueError, TypeError, AttributeError):
        return False


def _source_header(record, existing, original=None):
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
                or (datetime.fromisoformat(existing["played_at"].replace("Z", "+00:00")) !=
                    datetime.fromisoformat(record.played_at.replace("Z", "+00:00"))
                    and not (record.provider == "lichess" and existing["provider"] == "lichess"
                        and existing["provider_game_id"] == record.provider_game_id
                        and original is not None and original.source_game_id == record.provider_game_id
                        and legacy_lichess_precision_matches(original, existing["played_at"])) )):
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


def _fence_history_configuration(database, spec, expected):
    if (expected.workspace_id, expected.provider, expected.revision,
            expected.account.casefold(), expected.repertoire_ids) != (
            spec.workspace_id, spec.provider, spec.configuration_revision,
            spec.account, spec.repertoire_ids):
        raise ValueError("history_precision_saved_scope_changed")
    row = database.execute("""SELECT payload::text FROM product_feedback_configuration
        WHERE workspace_id=%s AND provider=%s FOR SHARE""", (spec.workspace_id, spec.provider)).fetchone()
    if row is None or SyncConfiguration.model_validate_json(row[0]) != expected:
        raise ValueError("history_precision_saved_scope_changed")


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
            header = _source_header(prepared, _current(database, prepared), original=record)
        return _bound_game(game, header)

    def occurrence_compatible(self, verified, bound, *, record, spec):
        return (spec.provider == "lichess" and bound.source_id == "lichess"
            and verified.played_at == record.played_at
            and legacy_lichess_precision_matches(record, bound.played_at))

    def recover_initial_history_precision(self, *, spec, record, verified, configuration):
        """Resolve only a newly captured, effect-free history precision conflict.

        Recheck the original page, source/capture, canonical row and absence of
        every delivery under the Games owner's existing locks. The original
        quarantined capture remains immutable; the additional capture is the
        durable resolution evidence. Anything else remains quarantined.
        """
        if spec.purpose != "history" or spec.provider != "lichess" or spec.stream != "manual":
            raise ValueError("history_precision_scope_mismatch")
        key = stable_id(spec.workspace_id, spec.provider, record.source_game_id)
        prepared = _prepare(record, spec)
        if (verified.workspace_id, verified.source_id, verified.source_revision,
                verified.source_digest, verified.played_at, verified.starting_fen,
                list(verified.moves), verified.learner_color) != (
                spec.workspace_id, spec.provider, record.source_revision, record.source_digest,
                record.played_at, prepared.start_fen, prepared.uci_moves, prepared.color):
            raise ValueError("history_precision_verified_source_mismatch")
        with self._transaction() as raw:
            _fence_history_configuration(raw, spec, configuration)
            database = PostgresConnection(raw)
            storage.lock_import_identity(database, f"lichess:{prepared.provider_game_id}")
            existing = _current(raw, prepared, for_update=True)
            if existing is None or not legacy_lichess_precision_matches(record, existing["played_at"]):
                raise ValueError("history_precision_canonical_proof_missing")
            if datetime.fromisoformat(existing["played_at"].replace("Z", "+00:00")) == datetime.fromisoformat(record.played_at.replace("Z", "+00:00")):
                raise ValueError("history_precision_mismatch_required")
            try:
                header = _source_header(prepared, existing, original=record)
            except ValueError:
                raise ValueError("history_precision_canonical_source_conflict") from None
            canonical = _bound_game(verified, header)
            rows = raw.execute("SELECT payload::text FROM product_feedback_jobs WHERE job_id=%s FOR UPDATE", (spec.job_id,)).fetchall()
            job = JobState.model_validate_json(rows[0][0]) if len(rows) == 1 else None
            if (job is None or job.spec != spec or job.status != "quarantined"
                    or job.error != "unresolved_page_boundary" or job.continuation is not None
                    or job.last_checkpoint_at is not None or job.pages_committed != 1 or job.rejections):
                raise ValueError("history_precision_job_not_initial_quarantine")
            pages = raw.execute("""SELECT page_json::text FROM product_feedback_pages
                WHERE job_id=%s AND input_continuation IS NULL FOR SHARE""", (spec.job_id,)).fetchall()
            if len(pages) != 1:
                raise ValueError("history_precision_page_unavailable")
            page = json.loads(pages[0][0])
            matches = [item for item in page.get("records", []) if item.get("source_game_id") == record.source_game_id]
            if (page.get("provider") != spec.provider or page.get("account") != spec.account
                    or page.get("window") != spec.window.model_dump(mode="json")
                    or len(matches) != 1 or matches[0] != record.model_dump(mode="json")):
                raise ValueError("history_precision_original_page_mismatch")
            rows = raw.execute("SELECT payload::text FROM product_feedback_sources WHERE source_key=%s FOR UPDATE", (key,)).fetchall()
            state = SourceState.model_validate_json(rows[0][0]) if len(rows) == 1 else None
            if (state is None or state.version != 1 or not state.quarantined
                    or state.diagnostic != "unresolved_parsing_or_comparison_boundary"
                    or state.original.model_dump(mode="json") != record.model_dump(mode="json", exclude={"source_metadata"})
                    or state.record_fingerprint != stable_id(record.model_dump(mode="json", exclude={"source_revision"}))
                    or state.normalized != verified or state.findings or state.effects or state.encounters
                    or state.first_departure is not None or state.determined_at is not None):
                raise ValueError("history_precision_source_changed_or_has_effects")
            captures = raw.execute("SELECT payload::text FROM product_feedback_captures WHERE source_key=%s FOR SHARE", (key,)).fetchall()
            if len(captures) != 1:
                raise ValueError("history_precision_capture_not_unique")
            capture = Capture.model_validate_json(captures[0][0])
            if (capture.diagnostic != state.diagnostic or capture.record != state.original
                    or capture.normalized != verified or capture.findings or capture.encounters
                    or capture.first_departure is not None or json.loads(capture.provider_record_json) != matches[0]):
                raise ValueError("history_precision_capture_mismatch")
            if raw.execute("SELECT 1 FROM product_feedback_deliveries WHERE source_key=%s LIMIT 1", (key,)).fetchone() is not None:
                raise ValueError("history_precision_delivery_exists")
            revision = stable_id("legacy-lichess-second-resolution-v1", key, state.record_fingerprint, canonical.source_digest)
            evidence = capture.model_copy(update={"comparison_revision":revision, "normalized":canonical,
                "diagnostic":"legacy_lichess_second_resolution_v1"})
            raw.execute("""INSERT INTO product_feedback_captures
                (source_key,source_revision,source_digest,comparison_revision,payload)
                VALUES(%s,%s,%s,%s,%s::jsonb)""", (key, record.source_revision,
                    record.source_digest, revision, evidence.model_dump_json()))
            recovered = state.model_copy(update={"version":state.version+1, "normalized":canonical,
                "quarantined":False, "diagnostic":"legacy_lichess_second_resolution_v1"})
            changed = raw.execute("""UPDATE product_feedback_sources SET version=%s,payload=%s::jsonb
                WHERE source_key=%s AND version=%s""", (recovered.version, recovered.model_dump_json(), key, state.version)).rowcount
            if changed != 1:
                raise ConcurrentUpdate("history_precision_source_changed")
            return key

    def resume_initial_history_page(self, *, spec, page_document, configuration):
        """Resume only after every original first-page source is present and safe."""
        if spec.provider != "lichess" or spec.purpose != "history" or spec.stream != "manual":
            raise ValueError("history_precision_scope_mismatch")
        with self._transaction() as raw:
            _fence_history_configuration(raw, spec, configuration)
            rows = raw.execute("SELECT payload::text FROM product_feedback_jobs WHERE job_id=%s FOR UPDATE", (spec.job_id,)).fetchall()
            job = JobState.model_validate_json(rows[0][0]) if len(rows) == 1 else None
            if (job is None or job.spec != spec or job.status != "quarantined"
                    or job.error != "unresolved_page_boundary" or job.continuation is not None
                    or job.last_checkpoint_at is not None or job.pages_committed != 1 or job.rejections):
                raise ValueError("history_precision_job_not_initial_quarantine")
            pages = raw.execute("""SELECT page_json::text,page_key FROM product_feedback_pages
                WHERE job_id=%s AND input_continuation IS NULL FOR SHARE""", (spec.job_id,)).fetchall()
            if len(pages) != 1:
                raise ValueError("history_precision_page_unavailable")
            page = json.loads(page_document)
            if (page != json.loads(pages[0][0]) or pages[0][1] != hashlib.sha256(
                    ("\0" + page_document).encode()).hexdigest()):
                raise ValueError("history_precision_page_replay_identity_mismatch")
            records = page.get("records")
            if (page.get("provider") != spec.provider or page.get("account") != spec.account
                    or page.get("window") != spec.window.model_dump(mode="json")
                    or not isinstance(records,list) or not records or len(records) > 100):
                raise ValueError("history_precision_original_page_mismatch")
            identities = [stable_id(spec.workspace_id, spec.provider, item.get("source_game_id")) for item in records]
            if len(set(identities)) != len(identities):
                raise ValueError("history_precision_duplicate_source")
            rows = raw.execute("""SELECT payload::text FROM product_feedback_sources
                WHERE source_key=ANY(%s) ORDER BY source_key FOR UPDATE""", (identities,)).fetchall()
            states = {state.source_key:state for row in rows if (state:=SourceState.model_validate_json(row[0]))}
            if set(states) != set(identities):
                raise ValueError("history_precision_page_sources_missing")
            for identity, original in zip(identities, records):
                state = states[identity]
                without_revision = {key:value for key,value in original.items() if key != "source_revision"}
                without_metadata = {key:value for key,value in original.items() if key != "source_metadata"}
                if (state.quarantined or state.normalized is None
                        or state.record_fingerprint != stable_id(without_revision)
                        or state.original.model_dump(mode="json") != without_metadata):
                    raise ValueError("history_precision_page_sources_unresolved")
            resumed = job.model_copy(update={"status":"queued", "error":"history_precision_replay_saved_page", "lease_until":0})
            self._save_job(raw, resumed)
            return len(records)

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
                    capture = captures[source.source_key]
                    header = _source_header(record, existing, original=capture.record)
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
