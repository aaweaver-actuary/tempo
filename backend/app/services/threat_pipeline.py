"""Durable, one-ply-at-a-time defensive-threat derivation and validation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
import chess

from tempo_legacy_database.connections import background_read_connection, connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.threats import pipeline as storage
from .activity_gate import activity_gate
from .background_activity import claimable, control_order
from .defensive_analysis import search_admission_sql, analysis_enabled, recommendation_request_ids_sql
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    enqueue_task,
    enqueue_task_in_transaction,
    lock_current_slice,
)
from .threat_detection import (
    find_defensive_knight_forks, propose_exercise_anchors, trace_knight_route,
)
from .threat_models import (
    ExerciseAnchor, ForkGeometry, ForkTarget, GameSnapshot, PositionContext,
    SourceLine, ThreatPolicy, ThreatSeed,
)
from .threat_validation import (
    AnalysisLine, AnalysisReport, AnalysisRequest, EngineScore,
    make_validation_plan, validate_threat_anchor,
)


DETECTOR_VERSION = 1
ENGINE_VERSION = "Stockfish 19 WASM"
NETWORK_VERSION = "nn-61e7af4bb97d.nnue"
POLICY = ThreatPolicy()

from tempo_legacy_database.adapters.threats.pipeline import THREAT_CANDIDATE_UPSERT_SQL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _request_from_json(raw: dict) -> AnalysisRequest:
    return AnalysisRequest(**{**raw, "position_prefix_uci": tuple(raw["position_prefix_uci"])})


def report_from_json(raw: dict) -> AnalysisReport:
    return AnalysisReport(
        request=_request_from_json(raw["request"]),
        lines=tuple(AnalysisLine(
            root_move_uci=line["root_move_uci"],
            pv_uci=tuple(line["pv_uci"]),
            score=EngineScore(**line["score"]),
            depth=line["depth"],
        ) for line in raw["lines"]),
        complete=bool(raw["complete"]),
    )


def validate_analysis_report(request: AnalysisRequest, report: AnalysisReport) -> None:
    """Reject incomplete or misattributed engine output before it enters durable evidence."""
    if report.request != request or not report.complete or not report.lines:
        raise ValueError("Engine report is incomplete or belongs to another request")
    board = chess.Board(request.position_start_fen)
    for move_uci in request.position_prefix_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Engine request has an illegal position history")
        board.push(move)
    if request.root_move_uci and len(report.lines) != 1:
        raise ValueError("Restricted search returned multiple root moves")
    for line in report.lines:
        if (line.depth < request.depth or not line.pv_uci
                or line.root_move_uci != line.pv_uci[0]
                or request.root_move_uci and line.root_move_uci != request.root_move_uci):
            raise ValueError("Engine report has insufficient depth or the wrong root move")
        position = board.copy(stack=False)
        for move_uci in line.pv_uci:
            move = chess.Move.from_uci(move_uci)
            if move not in position.legal_moves:
                raise ValueError("Engine report contains an illegal principal variation")
            position.push(move)


def execute_threat_report_audit(task: dict) -> bool:
    """Audit one saved report and retain invalid evidence before requesting replacement."""
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    with background_read_connection() as database:
        row = storage.next_saved_report(database, cursor)
        item = dict(row) if row else None
    if not item:
        return False
    rejection_reason = None
    try:
        validate_analysis_report(
            _request_from_json(json.loads(item["request_json"])),
            report_from_json(json.loads(item["report_json"])),
        )
    except (TypeError, KeyError, ValueError) as error:
        rejection_reason = str(error)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return True
        else:
            lease = storage.task_lease(database, task["id"])
            if not lease or lease["generation"] != task["generation"] or lease["lease_token"] != task["lease_token"]:
                return True
        saved = storage.saved_report(database, item["id"])
        if (rejection_reason and saved and saved["state"] == "complete"
                and saved["report_json"] == item["report_json"]):
            storage.archive_report(database, item["id"], item["report_json"], rejection_reason, _now())
            storage.queue_report_repair(database, f"Repair: {rejection_reason}", _now(), item["id"])
            storage.reset_linked_candidates(database, _now(), item["id"])
            storage.block_linked_cards(database, item["id"])
        storage.advance_audit(
            database, json.dumps({"cursor": item["id"]}), _now(), _now(), task["id"], task["generation"],
            task["lease_token"],
        )
    return True


def _seed_from_json(raw: dict) -> ThreatSeed:
    geometry = raw["geometry"]
    return ThreatSeed(
        raw["seed_id"], raw["game_id"], raw["analysis_version"],
        SourceLine(**{**raw["source_line"],
                      "moves_uci": tuple(raw["source_line"]["moves_uci"])}),
        raw["fork_line_index"],
        ForkGeometry(**{**geometry,
                        "king": ForkTarget(**geometry["king"]),
                        "major": ForkTarget(**geometry["major"]),
                        "other_majors": tuple(ForkTarget(**target)
                                              for target in geometry.get("other_majors", ())) }),
    )


def _anchor_from_json(raw: dict) -> ExerciseAnchor:
    position = raw["position"]
    return ExerciseAnchor(
        raw["seed_id"], raw["player_ply"],
        PositionContext(position["start_fen"], tuple(position["prefix_uci"]),
                        position["learner_color"]),
        raw["historical_move_uci"], raw["decisions_before_event"],
    )


def enqueue_threat_scan(game_id: str, analysis_version: int, *, background: bool) -> None:
    enqueue_task(
        "defensive_threat_scan", game_id,
        {"game_id": game_id, "analysis_version": analysis_version, "cursor": 0},
        priority=145, foreground=not background,
    )


def enqueue_threat_backfill() -> dict:
    """Resume a one-game-at-a-time scan of previously analyzed imports."""
    return enqueue_task(
        "defensive_threat_backfill", "analyzed-games",
        {"phase": "games", "cursor": ""}, priority=160,
    )


def execute_threat_backfill_slice(task: dict) -> bool:
    """Publish one existing game scan or repertoire refresh, then yield."""
    phase = task["payload"].get("phase", "games")
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        if phase == "games":
            row = storage.next_analyzed_game(database, cursor)
        else:
            row = storage.next_repertoire(database, cursor, "__defense__")
        item = dict(row) if row else None
    if item is None and phase == "repertoires" and not postgres_store.configured():
        return False
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return False
            if item is None and phase == "repertoires":
                return complete_task_slice_in_transaction(database, task)
        else:
            lease = storage.task_lease(database, task["id"])
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return True
        if item is not None:
            enqueue_in_transaction = (
                enqueue_compact_postgres_task_in_transaction
                if postgres_store.configured() else enqueue_task_in_transaction
            )
            if phase == "games":
                enqueue_in_transaction(
                    database, "defensive_threat_scan", item["id"],
                    {"game_id": item["id"], "analysis_version": item["analysis_version"], "cursor": 0},
                    priority=145,
                )
            else:
                from .repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
                enqueue_opportunity_refresh_in_transaction(database, item['id'])
        next_phase = phase if item is not None else "repertoires"
        next_cursor = item["id"] if item is not None else ""
        if postgres_store.configured():
            return advance_task_slice_in_transaction(
                database, task, next_phase=next_phase,
                next_payload={"phase": next_phase, "cursor": next_cursor},
            )
        storage.advance_backfill(
            database, json.dumps({"phase": next_phase, "cursor": next_cursor}), _now(), _now(), task["id"],
            task["generation"], task["lease_token"],
        )
    return True


def _advance_scan(database, task: dict, next_cursor: int, *, seed_index: int = 0) -> None:
    payload = {**task["payload"], "cursor": next_cursor}
    if seed_index:
        payload["seed_index"] = seed_index
    else:
        payload.pop("seed_index", None)
    storage.advance_scan(
        database, json.dumps(payload), _now(), _now(), task["id"], task["generation"], task["lease_token"],
    )


def _upsert_seed(database, game: GameSnapshot, seed: ThreatSeed, *, prepared=None) -> None:
    incident_id = _digest({
        "game": game.game_id,
        "fork_ply": seed.fork_ply, "geometry": asdict(seed.geometry),
    })
    finding_id = _digest({"kind": "defensive tactical threat", "incident": incident_id})
    route, anchors, plans = prepared or _prepare_seed(game, seed)
    finding_evidence = {
        "subtype": "knightKingMajor", "incident_id": incident_id,
        "seed": asdict(seed), "knight_route": [asdict(hop) for hop in route],
        "detector_version": DETECTOR_VERSION,
    }
    existing_finding = storage.existing_finding(database, finding_id)
    if existing_finding:
        previous = json.loads(existing_finding["evidence_json"])
        previous_seed = previous["seed"]
        previous_rank = (
            previous_seed["source_line"]["origin"] == "played",
            previous_seed["source_line"]["start_ply"],
            previous_seed["seed_id"],
        )
        incoming_rank = (
            seed.source_line.origin == "played", seed.source_line.start_ply,
            seed.seed_id,
        )
        if previous_seed["analysis_version"] == game.analysis_version and incoming_rank <= previous_rank:
            return
    now = _now()
    storage.upsert_finding(
        database, finding_id, game.game_id, game.analysis_version, seed.source_line.start_ply,
        json.dumps(finding_evidence), now, now,
    )
    active_anchor_plies = {anchor.player_ply for anchor in anchors}
    for obsolete in storage.active_incident_candidates(database, incident_id):
        if obsolete["player_ply"] not in active_anchor_plies:
            storage.supersede_candidate(database, now, now, obsolete["id"])
            if obsolete["card_id"]:
                storage.block_card(database, obsolete["card_id"])
    for anchor in anchors:
        candidate_id = _digest({"incident": incident_id, "player_ply": anchor.player_ply})
        evidence = {"seed": asdict(seed), "anchor": asdict(anchor),
                    "knight_route": [asdict(hop) for hop in route]}
        fingerprint = _digest(evidence)
        old = storage.candidate_source(database, candidate_id)
        plan = plans[anchor.player_ply]
        storage.upsert_candidate(
            database, candidate_id, finding_id, game.game_id, game.analysis_version, incident_id,
            anchor.player_ply, json.dumps(evidence), fingerprint, DETECTOR_VERSION, json.dumps(asdict(POLICY)),
            now, now,
        )
        if old and old["source_fingerprint"] == fingerprint:
            continue
        if old and old["card_id"]:
            storage.block_card(database, old["card_id"])
        storage.clear_candidate_requests(database, candidate_id)
        for role, request in (("best", plan.best_request),
                              ("historical", plan.historical_request)):
            storage.retain_analysis_request(database, request.request_id, json.dumps(asdict(request)), now, now)
            storage.retain_candidate_request(database, candidate_id, request.request_id, role)


def _prepare_seed(game: GameSnapshot, seed: ThreatSeed):
    """Keep chess traversal and validation planning outside the write transaction."""
    route = trace_knight_route(game, seed, max_hops=POLICY.max_knight_hops)
    anchors = propose_exercise_anchors(game, seed, POLICY)
    plans = {
        anchor.player_ply: make_validation_plan(
            anchor, engine_version=ENGINE_VERSION,
            network_version=NETWORK_VERSION, policy=POLICY,
        ) for anchor in anchors
    }
    return route, anchors, plans


def execute_threat_scan_slice(task: dict) -> bool:
    """Scan one actual game ply and its bounded saved candidate lines, then yield."""

    game_id = task["payload"]["game_id"]
    cursor = int(task["payload"]["cursor"])
    version = int(task["payload"]["analysis_version"])
    activity_gate.wait_for_foreground()
    with background_read_connection() as database:
        row = storage.scan_game(database, game_id)
        candidate_rows = [dict(item) for item in storage.scan_candidate_lines(database, game_id, cursor)]
    if not row or row["analysis_version"] != version:
        return False
    game = GameSnapshot(row["id"], version, row["start_fen"],
                        tuple(json.loads(row["moves_json"])), row["color"])
    if cursor >= len(game.moves_uci):
        return False
    lines = [SourceLine("played", cursor, (game.moves_uci[cursor],))]
    lines.extend(SourceLine("engine", cursor, tuple(json.loads(item["principal_variation_json"])))
                 for item in candidate_rows)
    seeds: list[ThreatSeed] = []
    for source_line in lines:
        if not source_line.moves_uci:
            continue
        try:
            seeds.extend(find_defensive_knight_forks(game, source_line))
        except (ValueError, TypeError):
            # A malformed saved PV is never promoted into evidence.
            continue
    postgres_scan = postgres_store.configured()
    seed_index = int(task["payload"].get("seed_index", 0)) if postgres_scan else 0
    selected_seeds = seeds[seed_index:seed_index + 1] if postgres_scan else seeds
    prepared_seeds = [(seed, _prepare_seed(game, seed)) for seed in selected_seeds]
    ply_complete = not postgres_scan or seed_index + len(selected_seeds) >= len(seeds)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured() and not lock_current_slice(database, task):
            return True
        still_current = storage.current_game_version(database, game_id)
        if not still_current or still_current[0] != version:
            return False
        for seed, prepared in prepared_seeds:
            _upsert_seed(database, game, seed, prepared=prepared)
        if ply_complete:
            storage.supersede_old_generation(database, _now(), game_id, version)
        _advance_scan(database, task, cursor + 1 if ply_complete else cursor,
                      seed_index=0 if ply_complete else seed_index + 1)
    return True


def execute_threat_validation(task: dict) -> bool | None:
    candidate_id = task["payload"]["candidate_id"]
    activity_gate.wait_for_foreground()
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        row = storage.validation_candidate(database, candidate_id)
        reports = [dict(item) for item in storage.validation_reports(database, candidate_id)]
    if not row or row["superseded_at"] or row["analysis_version"] != row["current_version"]:
        if postgres_store.configured():
            with connection(background=True) as database:
                if lock_current_slice(database, task):
                    return complete_task_slice_in_transaction(database, task)
        return False
    evidence = json.loads(row["evidence_json"])
    seed = _seed_from_json(evidence["seed"])
    anchor = _anchor_from_json(evidence["anchor"])
    policy = ThreatPolicy(**json.loads(row["policy_json"]))
    plan = make_validation_plan(anchor, engine_version=ENGINE_VERSION,
                                network_version=NETWORK_VERSION, policy=policy)
    report_by_role = {}
    for item in reports:
        if not item["report_json"]:
            continue
        try:
            request = _request_from_json(json.loads(item["request_json"]))
            report = report_from_json(json.loads(item["report_json"]))
            validate_analysis_report(request, report)
        except (TypeError, KeyError, ValueError):
            continue
        report_by_role[item["role"]] = report
    result = validate_threat_anchor(
        anchor, seed, plan, report_by_role.get("best"),
        report_by_role.get("historical"), policy,
    )
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return False
            current = storage.locked_validation_candidate(database, candidate_id)
            if (not current or current["source_fingerprint"] != row["source_fingerprint"]
                    or current["analysis_version"] != row["analysis_version"]
                    or current["analysis_version"] != current["current_version"]
                    or current["superseded_at"]):
                return complete_task_slice_in_transaction(database, task)
        storage.save_validation(
            database, result.state, result.diagnostic, json.dumps(asdict(result)), _now(), candidate_id,
            row["source_fingerprint"],
        )
        if postgres_store.configured():
            if result.state in {"engine_supported", "validated_control"}:
                today = datetime.now().date().isoformat()
                enqueue_compact_postgres_task_in_transaction(
                    database, "defensive_admission", today,
                    {"queue_date": today, "phase": "threat", "cursor": "",
                     "control_exhausted": False}, priority=150,
                )
            return complete_task_slice_in_transaction(database, task)
    if result.state in {"engine_supported", "validated_control"}:
        from .threat_training import enqueue_defense_admission
        enqueue_defense_admission(background=True)
    return None


def enqueue_candidate_validation(candidate_id: str, *, background: bool) -> None:
    enqueue_task("defensive_threat_validate", candidate_id,
                 {"candidate_id": candidate_id}, priority=140,
                 foreground=not background)


def claim_analysis_request() -> dict | None:
    from .engine_scheduling import (lock_engine_scheduling_state, reserved_ordinary_game,
                                    record_automated_selection)
    now = _now()
    with connection(background=activity_gate.in_background) as database:
        storage.begin_claim(database)
        select_request = storage.request_selector(analysis_enabled(database))
        storage.requeue_expired_requests(database, now, now)
        row = select_request(database)
        if not row:
            return None
        interactive = storage.interactive_request(database, row['id'])
        if not interactive:
            automated_streak = lock_engine_scheduling_state(database)
            if automated_streak is None or reserved_ordinary_game(database, automated_streak, now):
                return None
        lease_id = str(uuid.uuid4())
        changed = storage.claim_request(
            database, lease_id, (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), now, row["id"],
        )
        if changed != 1:
            return None
        if not interactive:
            record_automated_selection(database)
        return {"id": row["id"], "request": json.loads(row["request_json"]),
                "lease_id": lease_id}


def save_analysis_report(request_id: str, lease_id: str, raw_report: dict) -> tuple[str, ...]:
    report = report_from_json(raw_report)
    if report.request.request_id != request_id:
        raise ValueError("Analysis report does not match its request")
    with read_connection() as database:
        saved_request = storage.saved_request_json(database, request_id)
    if not saved_request:
        raise KeyError("Analysis request not found")
    request_json = saved_request["request_json"]
    validate_analysis_report(_request_from_json(json.loads(request_json)), report)
    with connection(background=activity_gate.in_background) as database:
        row = storage.saved_request_state(database, request_id)
        if not row:
            raise KeyError("Analysis request not found")
        if row["state"] == "complete":
            return ()
        if row["state"] != "leased" or row["lease_id"] != lease_id:
            raise ValueError("Analysis lease is stale")
        if row["request_json"] != request_json:
            raise ValueError("Analysis request changed while report was checked")
        storage.complete_report(database, json.dumps(raw_report), _now(), request_id)
        candidate_ids = tuple(item[0] for item in storage.report_candidate_ids(database, request_id))
    return candidate_ids
