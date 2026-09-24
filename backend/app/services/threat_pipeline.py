"""Durable, one-ply-at-a-time defensive-threat derivation and validation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from ..database import connection, read_connection
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task
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


def _advance_scan(database, task: dict, next_cursor: int) -> None:
    payload = {**task["payload"], "cursor": next_cursor}
    database.execute(
        """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
              payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
              lease_expires_at=NULL,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        (json.dumps(payload), _now(), _now(), task["id"], task["generation"],
         task["lease_token"]),
    )


def _upsert_seed(database, game: GameSnapshot, seed: ThreatSeed) -> None:
    incident_id = _digest({
        "game": game.game_id, "analysis": game.analysis_version,
        "fork_ply": seed.fork_ply, "geometry": asdict(seed.geometry),
    })
    finding_id = _digest({"kind": "defensive tactical threat", "incident": incident_id})
    route = trace_knight_route(game, seed, max_hops=POLICY.max_knight_hops)
    finding_evidence = {
        "subtype": "knightKingMajor", "incident_id": incident_id,
        "seed": asdict(seed), "knight_route": [asdict(hop) for hop in route],
        "detector_version": DETECTOR_VERSION,
    }
    existing_finding = database.execute(
        "SELECT evidence_json FROM game_findings WHERE id=?", (finding_id,)
    ).fetchone()
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
        if incoming_rank <= previous_rank:
            return
    anchors = propose_exercise_anchors(game, seed, POLICY)
    now = _now()
    database.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,
                  evidence_json,motif,created_at,updated_at)
           VALUES(?,?,?,?, 'defensive tactical threat',0,?,'fork',?,?)
           ON CONFLICT(id) DO UPDATE SET evidence_json=excluded.evidence_json,
                  updated_at=excluded.updated_at""",
        (finding_id, game.game_id, game.analysis_version, seed.source_line.start_ply,
         json.dumps(finding_evidence), now, now),
    )
    active_anchor_plies = {anchor.player_ply for anchor in anchors}
    for obsolete in database.execute(
        """SELECT id,card_id,player_ply FROM threat_training_candidates
           WHERE incident_id=? AND superseded_at IS NULL""", (incident_id,),
    ).fetchall():
        if obsolete["player_ply"] not in active_anchor_plies:
            database.execute(
                "UPDATE threat_training_candidates SET superseded_at=?,updated_at=? WHERE id=?",
                (now, now, obsolete["id"]),
            )
            if obsolete["card_id"]:
                database.execute("UPDATE cards SET pending_validation=1 WHERE id=?",
                                 (obsolete["card_id"],))
    for anchor in anchors:
        candidate_id = _digest({"incident": incident_id, "player_ply": anchor.player_ply})
        evidence = {"seed": asdict(seed), "anchor": asdict(anchor),
                    "knight_route": [asdict(hop) for hop in route]}
        fingerprint = _digest(evidence)
        old = database.execute(
            "SELECT source_fingerprint,card_id FROM threat_training_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        plan = make_validation_plan(
            anchor, engine_version=ENGINE_VERSION,
            network_version=NETWORK_VERSION, policy=POLICY,
        )
        database.execute(
            """INSERT INTO threat_training_candidates(
                   id,finding_id,game_id,analysis_version,incident_id,player_ply,
                   evidence_json,source_fingerprint,detector_version,policy_json,
                   created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   evidence_json=excluded.evidence_json,
                   source_fingerprint=excluded.source_fingerprint,
                   superseded_at=NULL,
                   validation_state=CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN 'needs_analysis' ELSE validation_state END,
                   validation_json=CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN '{}' ELSE validation_json END,
                   dismissed_at=CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN NULL ELSE dismissed_at END,
                   dismissed_evidence_fingerprint=CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN NULL ELSE dismissed_evidence_fingerprint END,
                   approved_at=CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN NULL ELSE approved_at END,
                   exercise_revision=exercise_revision+CASE WHEN source_fingerprint!=excluded.source_fingerprint
                       THEN 1 ELSE 0 END,
                   updated_at=excluded.updated_at""",
            (candidate_id, finding_id, game.game_id, game.analysis_version,
             incident_id, anchor.player_ply, json.dumps(evidence), fingerprint,
             DETECTOR_VERSION, json.dumps(asdict(POLICY)), now, now),
        )
        if old and old["source_fingerprint"] == fingerprint:
            continue
        if old and old["card_id"]:
            database.execute(
                "UPDATE cards SET pending_validation=1 WHERE id=?", (old["card_id"],)
            )
        database.execute("DELETE FROM threat_candidate_requests WHERE candidate_id=?", (candidate_id,))
        for role, request in (("best", plan.best_request),
                              ("historical", plan.historical_request)):
            database.execute(
                """INSERT OR IGNORE INTO threat_analysis_requests(
                       id,request_json,created_at,updated_at) VALUES(?,?,?,?)""",
                (request.request_id, json.dumps(asdict(request)), now, now),
            )
            database.execute(
                """INSERT OR IGNORE INTO threat_candidate_requests(candidate_id,request_id,role)
                   VALUES(?,?,?)""", (candidate_id, request.request_id, role),
            )


def execute_threat_scan_slice(task: dict) -> bool:
    """Scan one actual game ply and its bounded saved candidate lines, then yield."""

    game_id = task["payload"]["game_id"]
    cursor = int(task["payload"]["cursor"])
    version = int(task["payload"]["analysis_version"])
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        row = database.execute(
            "SELECT id,analysis_version,start_fen,moves_json,color FROM imported_games WHERE id=?",
            (game_id,),
        ).fetchone()
        candidate_rows = [dict(item) for item in database.execute(
            """SELECT principal_variation_json FROM game_move_analysis_candidates
               WHERE game_id=? AND ply=? ORDER BY rank LIMIT 5""",
            (game_id, cursor),
        )]
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
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        still_current = database.execute(
            "SELECT analysis_version FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not still_current or still_current[0] != version:
            return False
        for seed in seeds:
            _upsert_seed(database, game, seed)
        database.execute(
            """UPDATE threat_training_candidates SET superseded_at=COALESCE(superseded_at,?)
               WHERE game_id=? AND analysis_version!=? AND superseded_at IS NULL""",
            (_now(), game_id, version),
        )
        _advance_scan(database, task, cursor + 1)
    return True


def execute_threat_validation(task: dict) -> None:
    candidate_id = task["payload"]["candidate_id"]
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        row = database.execute(
            """SELECT c.*,g.analysis_version current_version FROM threat_training_candidates c
               JOIN imported_games g ON g.id=c.game_id WHERE c.id=?""", (candidate_id,)
        ).fetchone()
        reports = [dict(item) for item in database.execute(
            """SELECT relation.role,request.report_json FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""",
            (candidate_id,),
        )]
    if not row or row["superseded_at"] or row["analysis_version"] != row["current_version"]:
        return
    evidence = json.loads(row["evidence_json"])
    seed = _seed_from_json(evidence["seed"])
    anchor = _anchor_from_json(evidence["anchor"])
    policy = ThreatPolicy(**json.loads(row["policy_json"]))
    plan = make_validation_plan(anchor, engine_version=ENGINE_VERSION,
                                network_version=NETWORK_VERSION, policy=policy)
    report_by_role = {
        item["role"]: report_from_json(json.loads(item["report_json"]))
        for item in reports if item["report_json"]
    }
    result = validate_threat_anchor(
        anchor, seed, plan, report_by_role.get("best"),
        report_by_role.get("historical"), policy,
    )
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute(
            """UPDATE threat_training_candidates
               SET validation_state=?,diagnostic=?,validation_json=?,updated_at=?
               WHERE id=? AND source_fingerprint=? AND superseded_at IS NULL""",
            (result.state, result.diagnostic, json.dumps(asdict(result)),
             _now(), candidate_id, row["source_fingerprint"]),
        )


def enqueue_candidate_validation(candidate_id: str, *, background: bool) -> None:
    enqueue_task("defensive_threat_validate", candidate_id,
                 {"candidate_id": candidate_id}, priority=140,
                 foreground=not background)


def claim_analysis_request() -> dict | None:
    now = _now()
    with connection(background=activity_gate.in_background) as database:
        database.execute(
            """UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,
                  lease_expires_at=NULL,updated_at=?
               WHERE state='leased' AND lease_expires_at<=?""", (now, now),
        )
        row = database.execute(
            """SELECT request.id,request.request_json FROM threat_analysis_requests request
               WHERE request.state='queued' AND EXISTS(
                   SELECT 1 FROM threat_candidate_requests relation
                   JOIN threat_training_candidates candidate ON candidate.id=relation.candidate_id
                   JOIN imported_games game ON game.id=candidate.game_id
                   WHERE relation.request_id=request.id AND candidate.superseded_at IS NULL
                     AND candidate.analysis_version=game.analysis_version
                     AND (candidate.validation_state='needs_analysis' OR relation.role='attempt'))
               ORDER BY request.created_at,request.id LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        lease_id = str(uuid.uuid4())
        database.execute(
            """UPDATE threat_analysis_requests SET state='leased',lease_id=?,
                  lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE id=? AND state='queued'""",
            (lease_id, (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
             now, row["id"]),
        )
        return {"id": row["id"], "request": json.loads(row["request_json"]),
                "lease_id": lease_id}


def save_analysis_report(request_id: str, lease_id: str, raw_report: dict) -> tuple[str, ...]:
    report = report_from_json(raw_report)
    if report.request.request_id != request_id:
        raise ValueError("Analysis report does not match its request")
    with connection(background=activity_gate.in_background) as database:
        row = database.execute(
            "SELECT state,lease_id FROM threat_analysis_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not row:
            raise KeyError("Analysis request not found")
        if row["state"] == "complete":
            return ()
        if row["state"] != "leased" or row["lease_id"] != lease_id:
            raise ValueError("Analysis lease is stale")
        database.execute(
            """UPDATE threat_analysis_requests SET state='complete',report_json=?,
                  lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE id=?""",
            (json.dumps(raw_report), _now(), request_id),
        )
        candidate_ids = tuple(item[0] for item in database.execute(
            "SELECT DISTINCT candidate_id FROM threat_candidate_requests WHERE request_id=?",
            (request_id,),
        ))
    return candidate_ids
