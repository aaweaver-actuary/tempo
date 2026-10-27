"""Opt-in Analysis publication storage; no connection, migration or worker setup.

The injected current-fence capability owns cross-owner reads and reservations.
This adapter touches only its own table. See README.md before
binding it to real owner capabilities; a fake fence does not make it atomic.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import hashlib
import json
from typing import Protocol

from pydantic import TypeAdapter
from pydantic_core import PydanticSerializationError

from app.analysis.jobs.ports import AnalysisFence, PreparedAnalysis

MAX_PUBLICATION_BYTES = 4 * 1024 * 1024
PUBLICATION_FORMAT_VERSION = 1
_PUBLICATION = TypeAdapter(PreparedAnalysis)

# Deliberately unregistered. The owning integration allocates any migration and
# installs this only in an explicitly disposable native contract until then.
SCHEMA_SQL = f"""
CREATE TABLE analysis_product_publications (
    repertoire_id TEXT PRIMARY KEY CHECK (length(repertoire_id) BETWEEN 1 AND 200),
    codec_version INTEGER NOT NULL CHECK (codec_version = {PUBLICATION_FORMAT_VERSION}),
    payload_json TEXT NOT NULL CHECK (octet_length(payload_json) BETWEEN 1 AND {MAX_PUBLICATION_BYTES}),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{{64}}$')
);
"""


class AnalysisPublicationError(ValueError):
    """A stable safe failure; never include stored payloads in error messages."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PublicationRow(Protocol):
    def __getitem__(self, index: int) -> object: ...


class PublicationCursor(Protocol):
    def fetchone(self) -> PublicationRow | None: ...


class PublicationDatabase(Protocol):
    """Database-driver seam, private to this SQL adapter, using qmark binding."""

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> PublicationCursor: ...


class CurrentAnalysisFence(Protocol):
    """Read actual facts using this SAME borrowed database and transaction.

    reserve=True must hold source/settings/personal/evidence owner reservations
    through the owning transaction's commit, including an initially absent
    publication row. Every corresponding writer must honor those reservations.
    A snapshot read, an unlocked generation query or a later recheck is not an
    implementation of this guarantee. Use the owners' established lock order.
    reserve=False still reads one internally consistent actual fence.
    """

    def __call__(self, database: PublicationDatabase, repertoire_id: str, *, reserve: bool) -> AnalysisFence: ...


TransactionFactory = Callable[[], AbstractContextManager[PublicationDatabase]]


def _bounded_bytes(payload: str) -> bytes:
    if type(payload) is not str:
        raise AnalysisPublicationError("analysis_publication_invalid")
    try:
        encoded = payload.encode("utf-8")
    except UnicodeError:
        raise AnalysisPublicationError("analysis_publication_invalid") from None
    if not 0 < len(encoded) <= MAX_PUBLICATION_BYTES:
        raise AnalysisPublicationError("analysis_publication_too_large")
    return encoded


def _canonical_json(result: PreparedAnalysis) -> str:
    return json.dumps(_PUBLICATION.dump_python(result, mode="json", warnings="error"),
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _require_ready_context(result: PreparedAnalysis) -> None:
    if result.status != "ready" or result.ranking is None:
        raise AnalysisPublicationError("analysis_publication_not_ready")
    ranking, fence = result.ranking, result.fence
    scope = ((fence.repertoire_id, fence.repertoire_revision),)
    if (ranking.source_revision != fence.source_revision
            or ranking.snapshot_digest != fence.snapshot_digest
            or ranking.gaps.based_on.repertoire_revisions != scope
            or ranking.introductions.based_on.repertoire_revisions != scope):
        raise AnalysisPublicationError("analysis_publication_context_mismatch")
    # Coverage policy and job controls have different settings-digest formats.
    # Preserve both; the complete job fence is checked against the owner facts.


def _encode_result(result: PreparedAnalysis) -> tuple[PreparedAnalysis, str]:
    try:
        if not isinstance(result, PreparedAnalysis):
            raise AnalysisPublicationError("analysis_publication_invalid")
        _require_ready_context(result)
        payload = _canonical_json(result)
        _bounded_bytes(payload)
        # JSON mode supports the public frozen dataclasses and strict tuple
        # models together. Detach inputs and validate even forged/model_copy
        # values before any transaction. Normalise e.g. integer float bounds.
        # Public nested models retain their own strictness and JSON tuple
        # handling. Exact canonical re-encoding below rejects coercion without
        # overriding the library models' validation mode from the outer codec.
        detached = _PUBLICATION.validate_json(payload)
        if detached != result:
            raise AnalysisPublicationError("analysis_publication_invalid")
        _require_ready_context(detached)
        payload = _canonical_json(detached)
        _bounded_bytes(payload)
        return detached, payload
    except AnalysisPublicationError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError, PydanticSerializationError):
        raise AnalysisPublicationError("analysis_publication_invalid") from None


def encode_publication(result: PreparedAnalysis) -> str:
    """Canonical, finite UTF-8 JSON of the actual public value; no copied DTO."""
    return _encode_result(result)[1]


def decode_publication(payload: str, expected_sha256: str) -> PreparedAnalysis:
    """Refuse corrupt, oversized, noncanonical or incompatible stored values."""
    encoded = _bounded_bytes(payload)
    if type(expected_sha256) is not str or hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise AnalysisPublicationError("analysis_publication_integrity_mismatch")
    try:
        result = _PUBLICATION.validate_json(payload)
        _require_ready_context(result)
        if _canonical_json(result) != payload:
            # Also refuses ignored dataclass fields, duplicate keys and NaN.
            raise AnalysisPublicationError("analysis_publication_invalid")
        return result
    except AnalysisPublicationError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError, PydanticSerializationError):
        raise AnalysisPublicationError("analysis_publication_invalid") from None


class PostgresAnalysisPublications:
    """Own short transactions supplied by outer composition; never a pool.

    The factory must commit on normal exit and roll back on failure, release
    the connection and all owner reservations before returning, and enforce the
    optional Analysis connection/lock/statement budgets. It must not yield a
    nested transaction whose commit/reservations outlive this method.
    """

    def __init__(self, transactions: TransactionFactory, *, current_fence: CurrentAnalysisFence):
        self._transactions = transactions
        self._current_fence = current_fence

    def _current(self, database: PublicationDatabase, repertoire_id: str, *, reserve: bool) -> AnalysisFence:
        supplied = self._current_fence(database, repertoire_id, reserve=reserve)
        if not isinstance(supplied, AnalysisFence):
            raise AnalysisPublicationError("analysis_current_fence_invalid")
        try:
            fence = AnalysisFence.model_validate_json(supplied.model_dump_json(), strict=True)
        except ValueError:
            raise AnalysisPublicationError("analysis_current_fence_invalid") from None
        if fence.repertoire_id != repertoire_id:
            raise AnalysisPublicationError("analysis_current_fence_scope_mismatch")
        return fence

    def current(self, repertoire_id: str) -> AnalysisFence:
        """Actual supplied owner facts, never publication/cache metadata."""
        with self._transactions() as database:
            return self._current(database, repertoire_id, reserve=False)

    def publish_if_current(self, result: PreparedAnalysis) -> bool:
        if isinstance(result, PreparedAnalysis) and result.status != "ready":
            return False
        prepared, payload = _encode_result(result)
        repertoire_id = prepared.fence.repertoire_id
        sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._transactions() as database:
            if self._current(database, repertoire_id, reserve=True) != prepared.fence:
                return False
            row = database.execute(
                "SELECT codec_version,payload_json,payload_sha256 FROM analysis_product_publications "
                "WHERE repertoire_id=? FOR UPDATE", (repertoire_id,)).fetchone()
            if row is not None and tuple(row[index] for index in range(3)) == (
                    PUBLICATION_FORMAT_VERSION, payload, sha256):
                return True
            database.execute(
                "INSERT INTO analysis_product_publications(repertoire_id,codec_version,payload_json,payload_sha256) "
                "VALUES(?,?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET "
                "codec_version=excluded.codec_version,payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256 "
                "WHERE analysis_product_publications.codec_version<>excluded.codec_version "
                "OR analysis_product_publications.payload_json<>excluded.payload_json "
                "OR analysis_product_publications.payload_sha256<>excluded.payload_sha256",
                (repertoire_id, PUBLICATION_FORMAT_VERSION, payload, sha256))
        return True

    @staticmethod
    def _decode_row(row: PublicationRow | None, repertoire_id: str) -> PreparedAnalysis | None:
        if row is None:
            return None
        if type(row[0]) is not int or row[0] != PUBLICATION_FORMAT_VERSION:
            raise AnalysisPublicationError("analysis_publication_format_unsupported")
        payload, sha256 = row[1], row[2]
        if not isinstance(payload, str) or not isinstance(sha256, str):
            raise AnalysisPublicationError("analysis_publication_invalid")
        result = decode_publication(payload, sha256)
        if result.fence.repertoire_id != repertoire_id:
            raise AnalysisPublicationError("analysis_publication_context_mismatch")
        return result

    def current_result(self, repertoire_id: str) -> PreparedAnalysis | None:
        """Current at the reserved read transaction; later delivery rechecks it."""
        with self._transactions() as database:
            fence = self._current(database, repertoire_id, reserve=True)
            row = database.execute(
                "SELECT codec_version,payload_json,payload_sha256 FROM analysis_product_publications "
                "WHERE repertoire_id=?", (repertoire_id,)).fetchone()
        result = self._decode_row(row, repertoire_id)
        return result if result is not None and result.fence == fence else None

    def latest_result(self, repertoire_id: str) -> PreparedAnalysis | None:
        """Explicit historical fallback, retaining its original fence verbatim."""
        with self._transactions() as database:
            row = database.execute(
                "SELECT codec_version,payload_json,payload_sha256 FROM analysis_product_publications "
                "WHERE repertoire_id=?", (repertoire_id,)).fetchone()
        return self._decode_row(row, repertoire_id)
