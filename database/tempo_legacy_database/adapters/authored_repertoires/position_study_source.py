"""Prepared position source and borrowed-writer authored-content fence.

Only the owner's preparation function constructs the ephemeral capability.
The full publication digest records original provenance, not current physical
bindings. The distinct authored digest is rechecked at the writer fence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from tempo_evidence_math.position_learning import Hold, SourceCertificate
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_game_evidence_contracts.legacy_scope import LegacyIntegrityCertificate

from .product_snapshot import read_repertoire

_HEADER = ("id", "scope_source_revision", "canonical_prefix_revision", "canonical_prefix_moves_json",
           "graph_generation", "graph_state", "workspace_id")
_LINE = ("id", "repertoire_id", "trained_color", "start_fen", "moves_json")
_OWNER_SEAL = object()

# This is the same graph lock/header/task/integrity law as the existing owner
# acceptance fence. No Games producer, card join, graph traversal, or new store.
_HEADER_SQL = """SELECT repertoire.id,repertoire.scope_source_revision,
 repertoire.canonical_prefix_revision,repertoire.canonical_prefix_moves_json,
 publication.generation graph_generation,publication.state graph_state,identity.workspace_id,
 integrity.status integrity_status,integrity.scan_status,integrity.scan_generation,
 integrity.checked_at,integrity.scan_completed_sources,integrity.scan_total_sources,integrity.scan_error,
 graph.generation graph_task_generation,graph.state graph_task_state,
 scan.id scan_task_id,scan.generation scan_task_generation,scan.state scan_task_state,scan.payload_json
 FROM repertoires repertoire
 JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
 CROSS JOIN legacy_learning_identity identity
 JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
 JOIN background_tasks graph ON graph.kind='opening_graph_rebuild' AND graph.deduplication_key=repertoire.id
 JOIN background_tasks scan ON scan.kind='integrity_scan' AND scan.deduplication_key=repertoire.id
 WHERE repertoire.id=%s AND identity.id=1"""
_LOCK = " FOR SHARE OF repertoire,publication,identity,integrity,graph,scan SKIP LOCKED"
_LINES_SQL = """SELECT CASE WHEN octet_length(id)<=65536 THEN id END id,
 CASE WHEN octet_length(repertoire_id)<=65536 THEN repertoire_id END repertoire_id,
 CASE WHEN octet_length(trained_color)<=65536 THEN trained_color END trained_color,
 CASE WHEN octet_length(start_fen)<=65536 THEN start_fen END start_fen,
 CASE WHEN octet_length(moves_json)<=65536 THEN moves_json END moves_json
 FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id LIMIT 10001
 FOR SHARE SKIP LOCKED"""


def _owner_header(database, repertoire_id, *, lock):
    row = database.execute_native(_HEADER_SQL + (_LOCK if lock else ""), (repertoire_id,)).fetchone()
    if row is None:
        raise ValueError("position_source_busy_or_missing")
    row = dict(row)
    try:
        certificate = LegacyIntegrityCertificate.model_validate({k: row[k] for k in LegacyIntegrityCertificate.model_fields})
        payload = json.loads(row["payload_json"])
        if (row["graph_state"] != "ready" or certificate.graph_task_generation != row["graph_generation"]
                or payload.get("repertoire_id") != repertoire_id
                or type(payload.get("graph_generation")) is not int
                or payload["graph_generation"] != row["graph_generation"]):
            raise ValueError("invalid_integrity_generation")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("position_source_integrity_unavailable") from exc
    return {k: row[k] for k in _HEADER}, certificate.model_dump(mode="json")


def _authored_digest(header, lines):
    # Exactly the source fields used by the route projection, independent of
    # physical cards, mappings, presentation/review data and capture time.
    selected = [{k: line[k] for k in _LINE} for line in lines]
    selected.sort(key=lambda line: line["id"])
    return fingerprint({"kind": "position_study_authored_v1", "header": header,
                        "variant": "standard", "policy": "repertoire_id", "lines": selected})


@dataclass(frozen=True)
class AuthoredSourceToken:
    original_snapshot_id: str
    original_source_revision: str
    original_source_digest: str
    repertoire_id: str
    authored_digest: str
    header_json: str
    integrity_json: str

    def as_dict(self):
        return {"original_snapshot_id": self.original_snapshot_id,
                "original_source_revision": self.original_source_revision,
                "original_source_digest": self.original_source_digest,
                "repertoire_id": self.repertoire_id, "authored_digest": self.authored_digest,
                "header": json.loads(self.header_json), "integrity": json.loads(self.integrity_json)}


@dataclass(frozen=True, init=False)
class PreparedPositionSource:
    snapshot: object
    routes: tuple
    authored_token: AuthoredSourceToken
    _seal: object

    def __init__(self, seal, snapshot, routes, token):
        if seal is not _OWNER_SEAL:
            raise ValueError("position_source_owner_preparation_required")
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "routes", routes)
        object.__setattr__(self, "authored_token", token)
        object.__setattr__(self, "_seal", seal)


@dataclass(frozen=True, init=False)
class PreparedPositionSourceWithFacts:
    """Owner-sealed source and detached semantic facts from its one owner read."""
    prepared: PreparedPositionSource
    _facts_json: str
    _seal: object

    def __init__(self, seal, prepared, facts_json):
        if seal is not _OWNER_SEAL:
            raise ValueError("position_source_owner_preparation_required")
        object.__setattr__(self, "prepared", prepared)
        object.__setattr__(self, "_facts_json", facts_json)
        object.__setattr__(self, "_seal", seal)

    def publication_facts(self):
        return json.loads(self._facts_json)


def _prepare_position_source_with_facts(database, repertoire_id):
    from app.repertoire.snapshot import PreparedPublishedRepertoire
    from app.repertoire.interfaces.position_schedule_occurrences import routes_from_snapshot
    try:
        facts = read_repertoire(database, repertoire_id)
        if facts["repertoire"]["id"] != repertoire_id:
            raise ValueError("position_source_scope_mismatch")
        published = PreparedPublishedRepertoire(facts)
        snapshot = published.snapshot
        header, integrity = _owner_header(database, repertoire_id, lock=False)
        if any(facts["repertoire"][k] != header[k] for k in _HEADER):
            raise ValueError("position_source_changed_during_preparation")
        membership = snapshot.membership
        provenance = SourceCertificate(membership.snapshot_id, membership.source_revision,
                                       membership.digest, True, True)
        routes = routes_from_snapshot(snapshot, source_certificate=provenance)
        if isinstance(routes, Hold):
            return routes
        token = AuthoredSourceToken(membership.snapshot_id, membership.source_revision, membership.digest,
            repertoire_id, _authored_digest(header, facts["lines"]),
            json.dumps(header, sort_keys=True, separators=(",", ":")),
            json.dumps(integrity, sort_keys=True, separators=(",", ":")))
        prepared = PreparedPositionSource(_OWNER_SEAL, snapshot, routes, token)
        return PreparedPositionSourceWithFacts(_OWNER_SEAL, prepared, json.dumps(published.publication_facts(), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    except (ValueError, TypeError, KeyError) as exc:
        return Hold("position_source_preparation_unavailable", str(exc))


def prepare_position_source_with_facts(database, repertoire_id: str) -> PreparedPositionSourceWithFacts | Hold:
    """Prepare source and preserve the same complete read, outside the writer."""
    return _prepare_position_source_with_facts(database, repertoire_id)


def prepare_position_source(database, repertoire_id: str) -> PreparedPositionSource | Hold:
    """Read and legally project a complete publication outside the writer."""
    result = _prepare_position_source_with_facts(database, repertoire_id)
    return result if isinstance(result, Hold) else result.prepared


def fence_position_source(database, prepared: PreparedPositionSource) -> Hold | None:
    """Fence authored content and current integrity in caller's writer transaction.

    Persist token.as_dict() with the immutable issued attempt. A successful fence
    does not claim that the original full physical publication digest is current.
    """
    if not isinstance(prepared, PreparedPositionSource) or prepared._seal is not _OWNER_SEAL:
        return Hold("position_source_owner_preparation_required")
    if database.read_only or database.raw.info.transaction_status.name != "INTRANS":
        return Hold("borrowed_owner_writer_transaction_required")
    token = prepared.authored_token
    membership = prepared.snapshot.membership
    if (membership.snapshot_id, membership.source_revision, membership.digest) != (
            token.original_snapshot_id, token.original_source_revision, token.original_source_digest):
        return Hold("position_source_provenance_mismatch")
    try:
        held = database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
                                       ("tempo:opening-graph:" + token.repertoire_id,)).fetchone()[0]
        if not held:
            return Hold("position_source_graph_busy")
        header, integrity = _owner_header(database, token.repertoire_id, lock=True)
        if header != json.loads(token.header_json) or integrity != json.loads(token.integrity_json):
            return Hold("position_source_authority_changed")
        lines = [dict(row) for row in database.execute_native(_LINES_SQL, (token.repertoire_id,)).fetchall()]
        if (len(lines) > 10000 or any(any(row.get(k) is None for k in _LINE) for row in lines)
                or _authored_digest(header, lines) != token.authored_digest):
            return Hold("position_source_authored_content_changed")
    except (ValueError, TypeError, KeyError) as exc:
        return Hold("position_source_fence_unavailable", str(exc))
    return None
