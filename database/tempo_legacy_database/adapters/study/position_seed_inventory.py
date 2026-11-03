"""Bounded, read-only-source inventory projection for position-owned Study.

This module consumes detached facts supplied by Repertoire and Study owners. It
never opens a connection or treats a physical binding as source currentness.
No live census is performed here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Mapping, TYPE_CHECKING

from app.repertoire.interfaces.position_schedule_occurrences import PositionInventoryFacts
from tempo_evidence_math.position_learning import Hold, SourceCertificate

if TYPE_CHECKING:
    from app.contracts.product_v02.repertoire import RepertoireSnapshot


@dataclass(frozen=True)
class DonorFact:
    """One physical-card state read from the Study owner in a read-only view."""

    card_id: str
    repertoire_id: str | None
    revision: int | None
    state: str | None
    due_date: str | None
    fsrs_card_json: str | None
    introduced_at: str | None
    archived: bool | None
    pending_validation: bool | None
    superseded_by: str | None
    integrity_blocked: bool | None
    owner_repertoire_ids: tuple[str, ...]
    card_exists: bool = True
    content_type: str | None = "opening"
    source_hold: str | None = None
    deleted: bool | None = None


@dataclass(frozen=True)
class SourceFacts:
    """Public snapshot and immutable detached Repertoire inventory or hold.

    A detached certificate is not authority: the owner prepares and fences the
    source separately in the seed writer transaction.
    """
    snapshot: "RepertoireSnapshot"
    inventory: PositionInventoryFacts | Hold | None
    source_hold: str | None = None


@dataclass(frozen=True)
class InventoryRequest:
    sources: tuple[SourceFacts, ...]
    donors: tuple[DonorFact, ...]
    measured_metrics: Mapping[str, object] | None = None


# Candidate IDs come only from the bounded, current Repertoire index. Each
# requested card is fetched by cards(id) primary key; cross-repertoire owners
# use repertoire_cards(card_id, repertoire_id) and include cards.repertoire_id.
CARD_PAGE_SQL = """
WITH requested AS (
    SELECT DISTINCT candidate.card_id
    FROM unnest(%s::text[]) AS candidate(card_id)
), facts AS (
    SELECT requested.card_id, c.repertoire_id, c.revision, c.state,
           c.due_date, c.fsrs_card_json, c.introduced_at, c.archived,
           c.pending_validation, c.superseded_by, c.content_type,
           (c.id IS NOT NULL) AS card_exists,
           EXISTS (SELECT 1 FROM deleted_cards d
                   WHERE d.card_id = requested.card_id) AS deleted,
           EXISTS (SELECT 1 FROM repertoire_integrity_card_blocks b
                   WHERE b.card_id = requested.card_id) AS integrity_blocked,
           ARRAY(SELECT owner_id FROM (
                     SELECT rc.repertoire_id AS owner_id
                     FROM repertoire_cards rc WHERE rc.card_id = requested.card_id
                     UNION SELECT c.repertoire_id WHERE c.repertoire_id IS NOT NULL
                 ) owners ORDER BY owner_id) AS owner_repertoire_ids
    FROM requested LEFT JOIN cards c ON c.id = requested.card_id
)
SELECT * FROM facts ORDER BY card_id
""".strip()


class InventoryHold(ValueError):
    pass


def _bindings_match_snapshot(snapshot, projection: PositionInventoryFacts) -> bool:
    """Check public detached bindings against their original owner snapshot."""
    expected = defaultdict(set)
    for mapping in snapshot.decision_presentations:
        key = (mapping.repertoire_id, snapshot.variant, mapping.position_key)
        expected[key].add((mapping.decision_id, mapping.presentation_id,
                           mapping.presentation_revision, mapping.available,
                           mapping.route_id, mapping.route_ply))
    actual = {}
    for position in projection.positions:
        key = (position.definition.key.repertoire_id, position.definition.key.variant,
               position.definition.key.position)
        if key in actual:
            return False
        actual[key] = {(b.decision_id, b.card_id, b.card_revision, b.available,
                        b.route_id, b.route_ply) for b in position.physical_bindings}
    return all(key in actual and actual[key] == bindings for key, bindings in expected.items()) and all(
        bindings == expected.get(key, set()) for key, bindings in actual.items())


def build_inventory(request: InventoryRequest) -> dict:
    """Aggregate owner facts without certifying currentness or donor validity.

    Repeated route occurrences collapse to one exact PositionKey. Routes and
    physical cards remain separate counts. Missing certificates and malformed
    source facts become holds; no unmeasured metric is estimated.
    """
    if not isinstance(request, InventoryRequest):
        raise TypeError("inventory_request_required")
    donor_by_id: dict[str, DonorFact] = {}
    for fact in request.donors:
        if not isinstance(fact, DonorFact) or not fact.card_id:
            raise InventoryHold("invalid_donor_source")
        if fact.card_id in donor_by_id and donor_by_id[fact.card_id] != fact:
            raise InventoryHold("conflicting_card_fact")
        donor_by_id[fact.card_id] = fact

    output = []
    seen_repertoires: set[str] = set()
    for source in sorted(request.sources, key=lambda item: item.snapshot.membership.scope_repertoire_ids):
        membership = source.snapshot.membership
        reps = tuple(membership.scope_repertoire_ids)
        if not reps or any(rep in seen_repertoires for rep in reps):
            raise InventoryHold("duplicate_or_empty_repertoire_scope")
        seen_repertoires.update(reps)
        projection = source.inventory
        if source.source_hold:
            source_hold = source.source_hold
        elif isinstance(projection, Hold):
            source_hold = projection.reason + (":" + projection.detail if projection.detail else "")
        elif not isinstance(projection, PositionInventoryFacts):
            source_hold = "authored_route_certificate_unavailable"
        else:
            scope = projection.source_scopes
            certificate = projection.source_certificate
            expected = (membership.snapshot_id, membership.source_revision, membership.digest,
                        membership.revision, membership.captured_at, membership.scope_id, reps)
            observed = (scope.snapshot_id, scope.source_revision, scope.source_digest,
                        scope.membership_revision, scope.captured_at, scope.scope_id, scope.repertoire_ids)
            if (observed != expected or not isinstance(certificate, SourceCertificate)
                    or (certificate.snapshot_id, certificate.source_revision, certificate.source_digest)
                       != expected[:3] or not certificate.complete or not certificate.current_integrity
                    or not membership.complete or not membership.available
                    or not _bindings_match_snapshot(source.snapshot, projection)):
                source_hold = "source_certificate_mismatch"
            else:
                source_hold = None
        for repertoire_id in reps:
            positions = (() if source_hold else tuple(p for p in projection.positions
                         if p.definition.key.repertoire_id == repertoire_id))
            position_rows = []
            card_ids_all: set[str] = set()
            donor_statuses = defaultdict(int)
            position_route_occurrences = 0
            repertoire_routes: set[str] = set()
            for position in positions:
                key = position.definition.key
                selected_response = position.definition.expected_uci
                route_ids = {occurrence.route_id for occurrence in position.occurrences}
                position_route_occurrences += len(position.occurrences)
                repertoire_routes.update(route_ids)
                card_revisions = defaultdict(set)
                card_availability = defaultdict(set)
                for binding in position.physical_bindings:
                    if binding.card_id:
                        card_revisions[binding.card_id].add(binding.card_revision)
                        card_availability[binding.card_id].add(binding.available)
                card_ids = set(card_revisions)
                card_ids_all.update(card_ids)
                position_holds = []
                candidate_cards = []
                eligible_donor_ids = []
                for card_id in sorted(card_ids):
                    fact = donor_by_id.get(card_id)
                    if card_availability[card_id] != {True}:
                        status = "binding_unavailable_or_ambiguous"
                    elif fact is None:
                        status = "missing_card_source"
                    else:
                        status = _donor_status(fact, repertoire_id, card_revisions[card_id])
                    donor_statuses[status] += 1
                    row = {"card_id": card_id, "status": status}
                    if fact is not None:
                        row.update(revision=fact.revision,
                                   shared_repertoire_ids=list(fact.owner_repertoire_ids))
                    candidate_cards.append(row)
                    if status == "source_eligible_unvalidated_fsrs_runtime":
                        eligible_donor_ids.append(card_id)
                    else:
                        position_holds.append(status)
                position_rows.append({
                    "position": key.position,
                    "variant": key.variant,
                    "selected_response_uci": selected_response,
                    "route_count": len(route_ids),
                    "physical_card_count": len(card_ids),
                    "physical_card_ids": sorted(card_ids),
                    "donor_candidates": candidate_cards,
                    "source_eligible_donor_card_ids": eligible_donor_ids,
                    "holds": sorted(set(position_holds)),
                })
            keys = positions
            output.append({
                "repertoire_id": repertoire_id,
                "source_status": "held" if source_hold else "source_facts_only",
                "source_hold": source_hold,
                "distinct_exact_positions": len(keys),
                "distinct_routes": len(repertoire_routes),
                "position_route_occurrences": position_route_occurrences,
                "distinct_physical_cards": len(card_ids_all),
                "shared_card_exclusions": sum(
                    1 for card_id in card_ids_all
                    if card_id in donor_by_id and len(set(donor_by_id[card_id].owner_repertoire_ids)) > 1),
                "donor_status_counts": dict(sorted(donor_statuses.items())),
                "physical_card_state_counts": _card_state_counts(card_ids_all, donor_by_id),
                "due_introduction_distribution": _introduction_distribution(
                    card_ids_all, donor_by_id),
                "positions": position_rows,
                "metrics": _metrics(request.measured_metrics),
            })
    return {"contract_version": "position-study-inventory.v1",
            "evidence_kind": "detached_source_facts",
            "live_database_read": False,
            "repertoires": output}


def _donor_status(fact: DonorFact, repertoire_id: str, expected_revisions: set[int]) -> str:
    if fact.source_hold:
        return fact.source_hold
    if fact.deleted is not False:
        return "deleted_or_unknown"
    if not fact.card_exists:
        return "missing_card_source"
    if fact.content_type != "opening":
        return "invalid_card_content_type"
    if fact.revision is None or fact.revision < 1:
        return "invalid_revision"
    if expected_revisions != {fact.revision}:
        return "card_revision_mismatch"
    if fact.fsrs_card_json is None:
        return "missing_fsrs_state"
    if not _fsrs_shape_valid(fact.fsrs_card_json):
        return "invalid_fsrs_state_shape"
    if fact.due_date is None:
        return "missing_due_date"
    try:
        date.fromisoformat(fact.due_date[:10])
    except (TypeError, ValueError):
        return "invalid_due_date"
    if fact.archived is not False:
        return "archived_or_unknown"
    if fact.pending_validation is not False:
        return "pending_validation_or_unknown"
    if fact.superseded_by is not None:
        return "superseded"
    if fact.integrity_blocked is not False:
        return "integrity_blocked_or_unknown"
    owners = set(fact.owner_repertoire_ids)
    if len(owners) > 1:
        return "shared_across_repertoires"
    if fact.repertoire_id not in (None, repertoire_id):
        return "wrong_repertoire_owner"
    if owners and owners != {repertoire_id}:
        return "owner_mismatch"
    if fact.introduced_at is None:
        return "unintroduced"
    if not isinstance(fact.introduced_at, str):
        return "invalid_introduction_timestamp"
    try:
        introduced = datetime.fromisoformat(fact.introduced_at.replace("Z", "+00:00"))
    except ValueError:
        return "invalid_introduction_timestamp"
    if introduced.utcoffset() is None:
        return "invalid_introduction_timestamp"
    return "source_eligible_unvalidated_fsrs_runtime"


def _metrics(measured: Mapping[str, object] | None) -> dict:
    # Explicitly unavailable unless an owner supplies a measured value.
    names = ("distinct_positions_tested_per_minute", "repeated_learner_moves_avoided",
             "nondue_first_response_failures", "retention_7d", "retention_30d",
             "migration_seconds", "hold_counts", "queue_seconds", "review_seconds")
    return {name: measured[name] if measured is not None and name in measured else None
            for name in names}


def read_card_page(database, *, candidate_card_ids: tuple[str, ...]):
    """Read one batch of mapped cards through an existing `tempo_reader` connection.

    The candidate IDs must come from the bounded Repertoire snapshot. The
    caller owns the connection and repeatable-read transaction and splits the
    candidate set into batches of at most 500 IDs. Missing IDs are returned as
    explicit `card_exists=false` rows. No whole-table repertoire scan occurs.
    """
    return _read_card_page(database, candidate_card_ids, required_role="tempo_reader")


def read_seed_donor_page(database, *, candidate_card_ids: tuple[str, ...]):
    """Prepare seed facts in the writer role's read-only repeatable-read transaction.

    The caller supplies bounded source-derived IDs. The later seed transaction
    must fence and re-read the donor and source; this is only a snapshot.
    """
    return _read_card_page(database, candidate_card_ids, required_role="tempo_writer")


def _read_card_page(database, candidate_card_ids, *, required_role):
    if (not isinstance(candidate_card_ids, tuple) or not candidate_card_ids
            or len(candidate_card_ids) > 500
            or any(not isinstance(value, str) or not value or len(value) > 200
                   for value in candidate_card_ids)
            or len(set(candidate_card_ids)) != len(candidate_card_ids)):
        raise InventoryHold("invalid_card_page_bounds")
    if getattr(database, "read_only", None) is not True:
        raise InventoryHold("read_only_connection_required")
    execute = getattr(database, "execute_native", None)
    if execute is None:
        raise InventoryHold("native_postgres_reader_required")
    row = execute("SELECT current_user, current_setting('transaction_read_only'), current_setting('transaction_isolation')").fetchone()
    try:
        role, transaction_read_only, isolation = row[0], row[1], row[2]
    except (TypeError, KeyError):
        role, transaction_read_only, isolation = (row["current_user"],
            row["transaction_read_only"], row["transaction_isolation"])
    if role != required_role or transaction_read_only != "on":
        raise InventoryHold("postgres_read_only_role_required")
    if isolation != "repeatable read":
        raise InventoryHold("repeatable_read_transaction_required")
    raw = getattr(database, "raw", None)
    status = getattr(getattr(raw, "info", None), "transaction_status", None)
    if status is None or getattr(status, "name", None) != "INTRANS":
        raise InventoryHold("active_read_transaction_required")
    return execute(CARD_PAGE_SQL, (list(candidate_card_ids),)).fetchall()


def _fsrs_shape_valid(raw: str) -> bool:
    """Check persisted donor fields without claiming native FSRS compatibility."""
    required = {"card_id", "state", "step", "stability", "difficulty", "due", "last_review"}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not required <= value.keys() or value["card_id"] is None:
            return False
        for field in ("due", "last_review"):
            if value[field] is None and field == "last_review":
                continue
            parsed = datetime.fromisoformat(value[field].replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                return False
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _introduction_distribution(card_ids: set[str], donors: Mapping[str, DonorFact]) -> dict:
    counts: Counter[str] = Counter()
    for card_id in card_ids:
        fact = donors.get(card_id)
        if fact is None or not fact.card_exists:
            counts["missing_card_source"] += 1
        elif fact.introduced_at is None:
            counts["unintroduced"] += 1
        elif _aware_timestamp(fact.introduced_at):
            counts["introduced"] += 1
        else:
            counts["invalid_or_unknown_introduction"] += 1
    return dict(sorted(counts.items()))


def _aware_timestamp(value: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except (ValueError, TypeError, AttributeError):
        return False


def _card_state_counts(card_ids: set[str], donors: Mapping[str, DonorFact]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for card_id in card_ids:
        fact = donors.get(card_id)
        if fact is None or not fact.card_exists:
            counts["missing_card_source"] += 1
            continue
        state = fact.state
        counts[state if state in {"locked", "new", "learning", "mature"} else "unknown_state"] += 1
    return dict(sorted(counts.items()))
