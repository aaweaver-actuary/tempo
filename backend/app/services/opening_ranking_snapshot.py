"""Opt-in authoritative read-only capture; never called by product queue reads."""
from __future__ import annotations

from datetime import date, timezone
import json
from time import monotonic

import psycopg
from psycopg_pool import PoolTimeout
import redis

from .. import postgres_store
from ..schema_version import POSTGRES_SCHEMA_VERSION
from .activity_gate import activity_gate
from .redis_admission_gate import BackgroundAdmissionDeferred
from .real_game_feedback import MISS_REASON, outstanding_miss_sql
from .opening_ranking_evaluation import (
    OpeningRankingCandidate, OpeningRankingContext, OpeningRankingError,
    canonical_json, snapshot_document,
)

MAX_CANDIDATES = 10_000
MAX_ROUTE_ROWS = 40_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
CAPTURE_SECONDS = 10
PRODUCTION_ORDER_BASIS = "production_planner_capacity_neutral_sorted_repertoire_then_card"


def _raw_rows(database, query, parameters, *, limit, remaining_bytes):
    """Bound transferred raw data before Python decoding or fingerprinting."""
    rows = database.execute_native(
        "SELECT CASE WHEN SUM(octet_length(row_to_json(raw)::text)) OVER ()<=%s "
        "THEN row_to_json(raw)::text ELSE NULL END AS row_json, "
        "SUM(octet_length(row_to_json(raw)::text)) OVER () AS total_bytes "
        f"FROM ({query} LIMIT %s) raw",
        (remaining_bytes, *parameters, limit + 1),
    ).fetchall()
    if len(rows) > limit or (rows and rows[0]["total_bytes"] > remaining_bytes):
        raise OpeningRankingError("limit_exceeded", "Shadow capture exceeds its row or 4 MiB evidence budget; narrow the repertoire selection.")
    return tuple(row["row_json"] for row in rows), int(rows[0]["total_bytes"]) if rows else 0


def _check_capture_available(deadline):
    if monotonic() >= deadline or activity_gate.foreground_waiting:
        raise OpeningRankingError("evaluation_busy", "Study work is active or the capture deadline expired; retry when study is idle.")


def capture_snapshot(*, study_day: str | None = None, repertoire_ids=()) -> dict:
    """Capture one current MVCC boundary, then calculate with no open connection."""
    current_day = date.today().isoformat()  # Same server-local day used by production queue planning.
    if study_day is not None and study_day != current_day:
        raise OpeningRankingError("historical_capture_unavailable", "Capture supports only the current production study day; replay a saved snapshot for an earlier boundary.")
    if not postgres_store.configured():
        raise OpeningRankingError("unsupported_backend", "Capture requires authoritative PostgreSQL configuration; file replay needs no database.")
    if any(not isinstance(identifier, str) or not identifier for identifier in repertoire_ids):
        raise OpeningRankingError("invalid_selection", "Repertoire IDs must be nonempty strings.")
    from .postgres_queue_refresh import _PRIORITY_OPENING_PAGE_SQL
    candidate_query = _PRIORITY_OPENING_PAGE_SQL.replace(
        "SELECT unnest(%s::text[]) AS card_id", outstanding_miss_sql(postgres=True),
    ).replace("SELECT * FROM cards WHERE id=ANY(%s::text[])",
              "SELECT * FROM cards WHERE content_type='opening' AND state IN ('new','locked') "
              "AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0")
    candidate_query = candidate_query.replace(
        "SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,",
        "SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,"
        "c.revision,c.start_fen,c.state,c.introduced_at,c.archived,c.pending_validation,"
        "linked.name AS repertoire_name,publication.generation AS priority_generation,"
        "COALESCE(published.scoring_version,legacy.scoring_version) AS scoring_version,"
        "COALESCE(published.updated_at,legacy.updated_at) AS priority_updated_at,"
        "COALESCE(published.evidence_json,legacy.evidence_json) AS priority_evidence_json,"
        "COALESCE(published.completion_mass,legacy.completion_mass) AS completion_mass,"
        "COALESCE(published.frontier_reach,legacy.frontier_reach) AS frontier_reach,",
    )
    parameters = (MISS_REASON, current_day, current_day, current_day, current_day)
    if repertoire_ids:
        candidate_query += " AND linked.id=ANY(%s::text[])"
        parameters += (list(sorted(set(repertoire_ids))),)
    candidate_query += " ORDER BY linked.id,c.id"
    eligible_cards = f"SELECT DISTINCT id FROM ({candidate_query}) eligible"
    eligible_repertoires = f"SELECT DISTINCT repertoire_id FROM ({candidate_query}) eligible"
    deadline = monotonic() + CAPTURE_SECONDS
    raw_groups = {}
    remaining_bytes = MAX_SNAPSHOT_BYTES
    try:
        with activity_gate.background_request(), activity_gate.background_database_section():
            _check_capture_available(deadline)
            with postgres_store.connection(read_only=True, authoritative=True, background=True,
                                           repeatable_read=True, pool_timeout_seconds=0.1) as database:
                captured_at = database.execute_native("SELECT transaction_timestamp() AS as_of").fetchone()["as_of"]
                queries = (
                    ("candidates", candidate_query, parameters, MAX_ROUTE_ROWS),
                    ("routes", "SELECT step.* FROM opening_graph_steps step JOIN opening_graph_publications publication "
                     "ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation "
                     f"WHERE step.card_id IN ({eligible_cards}) AND step.repertoire_id IN ({eligible_repertoires}) "
                     "ORDER BY step.repertoire_id,step.line_id,step.decision_index", parameters + parameters, MAX_ROUTE_ROWS),
                    ("repertoires", "SELECT repertoire.id,repertoire.name,COALESCE(repertoire.new_cards_per_day,settings.new_cards_per_day) AS daily_limit,"
                     "graph.generation AS graph_generation,graph.state AS graph_state,graph.published_at AS graph_published_at,"
                     "priority.generation AS priority_generation,priority.updated_at AS priority_published_at,"
                     "preparation.source_version,preparation.calculated_at,preparation.scoring_version,epoch.version AS priority_source_epoch "
                     "FROM repertoires repertoire CROSS JOIN settings LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=repertoire.id "
                     "LEFT JOIN repertoire_priority_publications priority ON priority.repertoire_id=repertoire.id "
                     "LEFT JOIN repertoire_priority_preparations preparation ON preparation.repertoire_id=repertoire.id AND preparation.generation=priority.generation "
                     "LEFT JOIN priority_repertoire_source_epochs epoch ON epoch.repertoire_id=repertoire.id "
                     f"WHERE settings.id=1 AND repertoire.id IN ({eligible_repertoires}) ORDER BY repertoire.id", parameters, MAX_ROUTE_ROWS),
                    ("introductions", "SELECT COALESCE(queue.admission_repertoire_id,card.repertoire_id) AS repertoire_id,COUNT(DISTINCT card.id) AS count "
                     "FROM daily_queue queue JOIN cards card ON card.id=queue.card_id "
                     "WHERE queue.queue_date=%s AND queue.cycle=0 AND card.content_type='opening' AND card.introduced_at=%s "
                     "GROUP BY COALESCE(queue.admission_repertoire_id,card.repertoire_id)", (current_day, current_day), MAX_ROUTE_ROWS),
                    ("global_source", "SELECT version FROM priority_source_epoch WHERE id=1", (), 1),
                )
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
    return _project_snapshot(decoded, captured_at, current_day, tuple(sorted(set(repertoire_ids))))


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


def _project_snapshot(decoded, captured_at, study_day, repertoire_ids):
    from ..main import _plan_prioritized_opening_admissions
    rows = decoded["candidates"]
    counts = {row["repertoire_id"]: row["count"] for row in decoded["introductions"]}
    limits = {row["id"]: row["daily_limit"] for row in decoded["repertoires"]}
    try:
        priority_order = _plan_prioritized_opening_admissions(rows, {}, study_day, len(rows))
        actual_plan = _plan_prioritized_opening_admissions(rows, counts, study_day, limits)
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
        candidates.append(OpeningRankingCandidate(row["id"], index, canonical_json(metadata)))
    if len(candidates) != len(metadata_by_card):
        raise OpeningRankingError("invalid_order", "Production diagnostic order did not cover the complete captured candidate set.")
    source_versions = {"postgres_schema_version": POSTGRES_SCHEMA_VERSION,
                       "priority_global_source": decoded["global_source"], "repertoires": decoded["repertoires"],
                       "introduction_counts": counts, "repertoire_selection": list(repertoire_ids),
                       "capture_boundary": "authoritative_repeatable_read_transaction"}
    context = OpeningRankingContext(captured_at.astimezone(timezone.utc).isoformat(), study_day, "",
                                    canonical_json(source_versions))
    admission_plan = [{"repertoire_id": repertoire_id, "card_id": row["id"],
                       "reason": row["gameplay_priority_reason"]} for repertoire_id, row in actual_plan]
    document = snapshot_document(candidates, context, admission_plan=admission_plan,
                                 production_order_basis=PRODUCTION_ORDER_BASIS,
                                 admission_plan_basis="production_planner_captured_limits_sorted_candidates_not_observed_queue_delivery")
    if len(canonical_json(document).encode()) > MAX_SNAPSHOT_BYTES:
        raise OpeningRankingError("limit_exceeded", "Projected snapshot exceeds 4 MiB; narrow the repertoire selection.")
    return document
