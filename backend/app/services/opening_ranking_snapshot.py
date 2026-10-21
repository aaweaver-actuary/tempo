"""Opt-in authoritative read-only capture; never called by product queue reads."""
from __future__ import annotations

from datetime import date, timezone
import json
import os
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg_pool import PoolTimeout
import redis

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.opening_ranking import storage
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from .activity_gate import activity_gate
from .redis_admission_gate import BackgroundAdmissionDeferred, configured as shared_admission_configured
from .real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_sql
from .opening_ranking_evaluation import (
    OpeningRankingCandidate, OpeningRankingContext, OpeningRankingError,
    canonical_json, snapshot_document, validate_policy_id,
)

MAX_CANDIDATES = 10_000
MAX_ROUTE_ROWS = 40_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
CAPTURE_SECONDS = 10
PRODUCTION_ORDER_BASIS = "production_planner_capacity_neutral_sorted_repertoire_then_card"


def _raw_rows(database, query, parameters, *, limit, remaining_bytes):
    """Bound transferred raw data before Python decoding or fingerprinting."""
    rows = storage.bounded_rows(database, query, parameters, limit=limit, remaining_bytes=remaining_bytes)
    if len(rows) > limit or (rows and rows[0]["total_bytes"] > remaining_bytes):
        raise OpeningRankingError("limit_exceeded", "Shadow capture exceeds its row or 4 MiB evidence budget; narrow the repertoire selection.")
    return tuple(row["row_json"] for row in rows), int(rows[0]["total_bytes"]) if rows else 0


def _check_capture_available(deadline):
    if monotonic() >= deadline or activity_gate.foreground_waiting:
        raise OpeningRankingError("evaluation_busy", "Study work is active or the capture deadline expired; retry when study is idle.")


def capture_snapshot(*, study_day: str | None = None, repertoire_ids=(),
                     production_timezone: str | None = None, policy_id: str | None = None) -> dict:
    """Capture one current MVCC boundary, then calculate with no open connection."""
    repertoire_ids = tuple(repertoire_ids)
    if policy_id is not None:
        validate_policy_id(policy_id)
        if repertoire_ids and set(repertoire_ids) != {policy_id}:
            raise OpeningRankingError("invalid_selection", "Selected policy capture requires only its repertoire.")
        repertoire_ids = (policy_id,)
    timezone_name = production_timezone if production_timezone is not None else os.getenv("TZ") or "America/New_York"
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise OpeningRankingError("invalid_production_timezone", "Provide the deployed Tempo TZ as a valid IANA timezone.") from error
    if study_day is not None:
        try:
            if not isinstance(study_day, str) or date.fromisoformat(study_day).isoformat() != study_day:
                raise ValueError("noncanonical study day")
        except (TypeError, ValueError) as error:
            raise OpeningRankingError("invalid_study_day", "Study day must be a valid ISO calendar date in YYYY-MM-DD format.") from error
    if not postgres_store.configured():
        raise OpeningRankingError("unsupported_backend", "Capture requires authoritative PostgreSQL configuration; file replay needs no database.")
    if not shared_admission_configured():
        raise OpeningRankingError("admission_unavailable", "Configure TEMPO_REDIS_URL for the deployed Tempo foreground admission service before capture; offline replay needs no service.")
    if any(not isinstance(identifier, str) or not identifier for identifier in repertoire_ids):
        raise OpeningRankingError("invalid_selection", "Repertoire IDs must be nonempty strings.")
    from .postgres_queue_refresh import _PRIORITY_OPENING_PAGE_SQL
    bind_capture_queries = storage.prepare_capture_queries(repertoire_ids, _PRIORITY_OPENING_PAGE_SQL)
    deadline = monotonic() + CAPTURE_SECONDS
    raw_groups = {}
    remaining_bytes = MAX_SNAPSHOT_BYTES
    try:
        with activity_gate.background_request(), activity_gate.background_database_section():
            _check_capture_available(deadline)
            with postgres_store.connection(read_only=True, authoritative=True, background=True,
                                           repeatable_read=True, pool_timeout_seconds=0.1) as database:
                captured_boundary = storage.capture_boundary(database, timezone_name)
                captured_at, current_day = captured_boundary["as_of"], captured_boundary["study_day"]
                if study_day is not None and study_day != current_day:
                    raise OpeningRankingError("historical_capture_unavailable", "The production day changed or the database clock differs; retry capture for the current production study day.")
                queries = bind_capture_queries(MISS_REASON, current_day, MAX_ROUTE_ROWS)
                for group_name, query, query_parameters, limit in queries:
                    _check_capture_available(deadline)
                    raw_groups[group_name], consumed = _raw_rows(database, query, query_parameters,
                                                                limit=limit, remaining_bytes=remaining_bytes)
                    remaining_bytes -= consumed
    except BackgroundAdmissionDeferred as error:
        raise OpeningRankingError("evaluation_busy", "Foreground work has priority; retry capture when study is idle.") from error
    except (psycopg.Error, PoolTimeout, redis.RedisError) as error:
        raise OpeningRankingError("capture_unavailable", "Read-only capture failed or exceeded the configured database budget; check PostgreSQL/Redis availability and retry when study is idle.") from error
    _check_capture_available(deadline)
    decoded = {name: [json.loads(raw) for raw in rows] for name, rows in raw_groups.items()}
    if len({row["id"] for row in decoded["candidates"]}) > MAX_CANDIDATES:
        raise OpeningRankingError("limit_exceeded", "Capture exceeds 10,000 physical candidates; narrow the repertoire selection.")
    return _project_snapshot(decoded, captured_at, current_day, tuple(sorted(set(repertoire_ids))),
                             production_timezone=timezone_name, policy_id=policy_id)


def _decoded_evidence(raw, expected_type, label, unavailable):
    if raw is None:
        unavailable.append(f"{label}:missing")
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, expected_type):
            raise ValueError("shape")
        return value
    except (TypeError, ValueError):
        unavailable.append(f"{label}:invalid")
        return None


def _project_snapshot(decoded, captured_at, study_day, repertoire_ids, *, production_timezone="America/New_York", policy_id=None):
    from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions
    from .real_game_feedback import MISS_REASON
    rows = decoded["candidates"]
    if policy_id is not None:
        rows = [row for row in rows if row["repertoire_id"] == policy_id]
        decoded = {**decoded,
                   "routes": [row for row in decoded["routes"] if row["repertoire_id"] == policy_id],
                   "repertoires": [row for row in decoded["repertoires"] if row["id"] == policy_id],
                   "introductions": [row for row in decoded["introductions"] if row["repertoire_id"] == policy_id]}
    counts = {row["repertoire_id"]: row["count"] for row in decoded["introductions"]}
    limits = {row["id"]: row["daily_limit"] for row in decoded["repertoires"]}
    try:
        priority_order = plan_prioritized_opening_admissions(rows, {}, study_day, len(rows), miss_reason=MISS_REASON)
        actual_plan = plan_prioritized_opening_admissions(rows, counts, study_day, limits, miss_reason=MISS_REASON)
    except (TypeError, ValueError, KeyError) as error:
        raise OpeningRankingError("invalid_production_input", "Captured inputs cannot reproduce the production planner; repair the stored card/priority evidence.") from error
    metadata_by_card = {}
    for row in rows:
        metadata = metadata_by_card.setdefault(row["id"], {
            "revision": row["revision"], "start_fen": row["start_fen"],
            "moves": json.loads(row["moves_json"]), "state": row["state"], "due_date": row["due_date"],
            "introduced_at": row["introduced_at"], "archived": row["archived"],
            "pending_validation": row["pending_validation"], "memberships": [], "routes": [],
        })
        unavailable = []
        line_ids = _decoded_evidence(row["completed_line_ids_json"], list, "completed_line_ids", unavailable)
        frontier = _decoded_evidence(row["frontier_decisions_json"], list, "frontier_decisions", unavailable)
        evidence = _decoded_evidence(row["priority_evidence_json"], dict, "priority_evidence", unavailable)
        frontier_ply = None
        if frontier:
            try:
                frontier_ply = max(int(item[2]["ply"]) for item in frontier)
            except (TypeError, ValueError, KeyError, IndexError):
                unavailable.append("frontier_ply:invalid")
        if frontier_ply is None:
            unavailable.append("frontier_ply:unavailable")
        metadata["memberships"].append({"repertoire_id": row["repertoire_id"], "repertoire_name": row["repertoire_name"],
            "priority_score": row["priority_score"], "completion_mass": row["completion_mass"],
            "frontier_reach": row["frontier_reach"], "completed_line_ids": line_ids,
            "frontier_decisions": frontier, "frontier_ply": frontier_ply, "priority_evidence": evidence,
            "priority_generation": row["priority_generation"], "scoring_version": row["scoring_version"],
            "priority_updated_at": row["priority_updated_at"], "gameplay_priority_reason": row["gameplay_priority_reason"],
            "priority_date": row["priority_date"], "unavailable_evidence": unavailable})
    for route in decoded["routes"]:
        metadata_by_card[route["card_id"]]["routes"].append(route)
    candidates = []
    for index, (repertoire_id, row) in enumerate(priority_order, 1):
        metadata = metadata_by_card[row["id"]]
        metadata["diagnostic_admission_repertoire_id"] = repertoire_id
        metadata["route_evidence_status"] = "available" if metadata["routes"] else "unavailable_current_published_routes"
        candidates.append(OpeningRankingCandidate(row["id"], index, canonical_json(metadata), policy_id=policy_id))
    if len(candidates) != len(metadata_by_card):
        raise OpeningRankingError("invalid_order", "Production diagnostic order did not cover the complete captured candidate set.")
    source_versions = {"postgres_schema_version": POSTGRES_SCHEMA_VERSION,
                       "priority_global_source": decoded["global_source"], "repertoires": decoded["repertoires"],
                       "introduction_counts": counts, "repertoire_selection": list(repertoire_ids),
                       "production_timezone": production_timezone,
                       "study_day_basis": "transaction_timestamp_in_declared_deployment_timezone",
                       "capture_boundary": "authoritative_repeatable_read_transaction"}
    context = OpeningRankingContext(captured_at.astimezone(timezone.utc).isoformat(), study_day, "",
                                    canonical_json(source_versions),
                                    evaluation_scope="selected_policy" if policy_id is not None else "global_diagnostic",
                                    policy_id=policy_id)
    admission_plan = [{"repertoire_id": repertoire_id, "card_id": row["id"],
                       "reason": row["gameplay_priority_reason"]} for repertoire_id, row in actual_plan]
    document = snapshot_document(candidates, context, admission_plan=admission_plan,
                                 production_order_basis=PRODUCTION_ORDER_BASIS,
                                 admission_plan_basis="production_planner_captured_limits_sorted_candidates_not_observed_queue_delivery")
    if len(canonical_json(document).encode()) > MAX_SNAPSHOT_BYTES:
        raise OpeningRankingError("limit_exceeded", "Projected snapshot exceeds 4 MiB; narrow the repertoire selection.")
    return document
