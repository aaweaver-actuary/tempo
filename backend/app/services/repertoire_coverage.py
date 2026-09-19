"""Deterministic repertoire coverage math and canonical position discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

import chess
import httpx

from ..database import connection
from .repertoire_comparison import canonical_fen
from .activity_gate import activity_gate


@dataclass(frozen=True)
class CoverageCandidate:
    move_uci: str
    probability: float


def fen_key(fen: str) -> str:
    return canonical_fen(fen)


def blend_probabilities(
    explorer_probability: float | None,
    maia_probability: float | None,
    *,
    explorer_games: int,
    prior_games: int = 200,
) -> float | None:
    if explorer_probability is None:
        return maia_probability
    if maia_probability is None:
        return explorer_probability
    explorer_weight = explorer_games / max(1, explorer_games + prior_games)
    return (
        explorer_weight * explorer_probability
        + (1 - explorer_weight) * maia_probability
    )


def required_reply_moves(
    candidates: list[CoverageCandidate],
    *,
    denominator: int,
    cumulative_target: float,
) -> set[str]:
    if denominator < 2:
        raise ValueError("Coverage denominator must be at least two")
    if not 0 < cumulative_target <= 1:
        raise ValueError("Cumulative coverage target must be between zero and one")
    positive_candidates = [candidate for candidate in candidates if candidate.probability > 0]
    total_probability = sum(candidate.probability for candidate in positive_candidates)
    if total_probability <= 0:
        return set()
    normalized = [
        CoverageCandidate(candidate.move_uci, candidate.probability / total_probability)
        for candidate in positive_candidates
    ]
    minimum_probability = 1 / denominator
    required = {
        candidate.move_uci
        for candidate in normalized
        if candidate.probability >= minimum_probability
    }
    cumulative_probability = 0.0
    for candidate in sorted(
        normalized, key=lambda item: (-item.probability, item.move_uci)
    ):
        if cumulative_probability >= cumulative_target:
            break
        required.add(candidate.move_uci)
        cumulative_probability += candidate.probability
    return required


def discover_opponent_positions(
    repertoire_lines: list[dict], horizon_fullmoves: int
) -> list[dict]:
    """Return one canonical opponent node per FEN, preserving all known routes."""
    positions: dict[str, dict] = {}
    maximum_plies = horizon_fullmoves * 2
    for line in repertoire_lines:
        board = chess.Board(line["start_fen"])
        trained_color = chess.WHITE if line["trained_color"] == "white" else chess.BLACK
        moves = json.loads(line["moves_json"])
        route: list[str] = []
        for ply in range(min(len(moves), maximum_plies) + 1):
            if board.turn != trained_color:
                key = fen_key(board.fen())
                position = positions.setdefault(
                    key,
                    {
                        "fen": board.fen(),
                        "fen_key": key,
                        "ply": ply,
                        "trained_color": line["trained_color"],
                        "routes": [],
                        "covered_replies": set(),
                    },
                )
                position["ply"] = min(position["ply"], ply)
                if route not in position["routes"]:
                    position["routes"].append(list(route))
                if ply < len(moves):
                    opponent_move = moves[ply]
                    response_index = ply + 1
                    if response_index < len(moves):
                        response_board = board.copy()
                        response_board.push_uci(opponent_move)
                        if response_board.turn == trained_color:
                            position["covered_replies"].add(opponent_move)
            if ply == min(len(moves), maximum_plies):
                break
            move = chess.Move.from_uci(moves[ply])
            if move not in board.legal_moves:
                break
            board.push(move)
            route.append(move.uci())
    return [
        {**position, "covered_replies": sorted(position["covered_replies"])}
        for position in sorted(
            positions.values(), key=lambda item: (item["ply"], item["fen_key"])
        )
    ]


EXPLORER_CACHE_MAX_AGE = timedelta(days=7)
SUPPORTED_RATING_BUCKETS = (1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nearest_rating_bucket(rating: int) -> int:
    return min(SUPPORTED_RATING_BUCKETS, key=lambda candidate: (abs(candidate - rating), candidate))


def enqueue_coverage_refresh(repertoire_id: str) -> str:
    with connection() as database:
        repertoire = database.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)
        ).fetchone()
        if not repertoire:
            raise KeyError("Repertoire not found")
        active = database.execute(
            """SELECT id FROM repertoire_coverage_runs
               WHERE repertoire_id=? AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 1""",
            (repertoire_id,),
        ).fetchone()
        if active:
            return active["id"]
        settings = database.execute("SELECT * FROM settings WHERE id=1").fetchone()
        lines = [
            dict(row)
            for row in database.execute(
                "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
                (repertoire_id,),
            )
        ]
        nodes = discover_opponent_positions(
            lines, int(settings["coverage_horizon_fullmoves"])
        )
        run_id = str(uuid.uuid4())
        now = _now()
        settings_payload = {
            "reply_denominator": settings["coverage_reply_denominator"],
            "cumulative_target": settings["coverage_cumulative_target"] / 100,
            "horizon_fullmoves": settings["coverage_horizon_fullmoves"],
            "path_floor": settings["coverage_path_floor"],
            "maia_elo": settings["coverage_maia_elo"],
        }
        database.execute(
            """INSERT INTO repertoire_coverage_runs(
                   id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at
               ) VALUES(?,?,?, ?,?,?,?)""",
            (
                run_id,
                repertoire_id,
                "queued" if nodes else "complete",
                json.dumps(settings_payload),
                len(nodes),
                now,
                now,
            ),
        )
        for node in nodes:
            node_id = hashlib.sha256(f"{run_id}\0{node['fen_key']}".encode()).hexdigest()
            database.execute(
                """INSERT INTO repertoire_coverage_nodes(
                       id,run_id,repertoire_id,fen,fen_key,ply,trained_color,
                       routes_json,covered_replies_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    node_id,
                    run_id,
                    repertoire_id,
                    node["fen"],
                    node["fen_key"],
                    node["ply"],
                    node["trained_color"],
                    json.dumps(node["routes"]),
                    json.dumps(node["covered_replies"]),
                    now,
                ),
            )
    return run_id


def claim_coverage_node() -> dict | None:
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute("BEGIN IMMEDIATE")
        node = database.execute(
            """SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id
               WHERE n.explorer_status='queued'
               ORDER BY r.created_at,n.ply,n.id LIMIT 1"""
        ).fetchone()
        if not node:
            return None
        changed = database.execute(
            "UPDATE repertoire_coverage_nodes SET explorer_status='running',updated_at=? WHERE id=? AND explorer_status='queued'",
            (_now(), node["id"]),
        ).rowcount
        if changed:
            database.execute(
                "UPDATE repertoire_coverage_runs SET status='running',updated_at=? WHERE id=?",
                (_now(), node["run_id"]),
            )
        return dict(node) if changed else None


def _cached_explorer_payload(
    database, fen: str, speeds: str, ratings: str
) -> tuple[dict | None, str]:
    cache_key = hashlib.sha256(f"{fen_key(fen)}\0{speeds}\0{ratings}".encode()).hexdigest()
    cached = database.execute(
        "SELECT response_json,fetched_at FROM explorer_position_cache WHERE cache_key=?",
        (cache_key,),
    ).fetchone()
    if cached and datetime.fromisoformat(cached["fetched_at"]) >= datetime.now(timezone.utc) - EXPLORER_CACHE_MAX_AGE:
        return json.loads(cached["response_json"]), cache_key
    return None, cache_key


def _fetch_explorer(fen: str, speeds: str, ratings: str) -> dict:
    with httpx.Client(timeout=15, headers={"User-Agent": "Tempo repertoire coverage/1.0"}) as client:
        response = client.get(
            "https://explorer.lichess.org/lichess",
            params={
                "variant": "standard",
                "fen": fen,
                "speeds": speeds,
                "ratings": ratings,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("moves"), list):
            raise ValueError("Lichess Explorer returned an invalid coverage response")
        return payload


def _recalculate_node(database, node_id: str, settings: dict) -> None:
    rows = database.execute(
        "SELECT * FROM repertoire_coverage_candidates WHERE node_id=?",
        (node_id,),
    ).fetchall()
    combined: list[CoverageCandidate] = []
    blended_by_move: dict[str, float | None] = {}
    for row in rows:
        blended = blend_probabilities(
            row["explorer_probability"],
            row["maia_probability"],
            explorer_games=int(row["explorer_probability"] is not None)
            * int(
                database.execute(
                    "SELECT explorer_games FROM repertoire_coverage_nodes WHERE id=?",
                    (node_id,),
                ).fetchone()[0]
            ),
        )
        blended_by_move[row["move_uci"]] = blended
        if blended is not None:
            combined.append(CoverageCandidate(row["move_uci"], blended))
    total = sum(candidate.probability for candidate in combined)
    if total:
        combined = [
            CoverageCandidate(candidate.move_uci, candidate.probability / total)
            for candidate in combined
        ]
    required = required_reply_moves(
        combined,
        denominator=int(settings["reply_denominator"]),
        cumulative_target=float(settings["cumulative_target"]),
    )
    normalized_by_move = {candidate.move_uci: candidate.probability for candidate in combined}
    for row in rows:
        source_state = (
            "blended"
            if row["explorer_probability"] is not None and row["maia_probability"] is not None
            else "explorer-only"
            if row["explorer_probability"] is not None
            else "maia-only"
            if row["maia_probability"] is not None
            else "unknown"
        )
        database.execute(
            """UPDATE repertoire_coverage_candidates
               SET blended_probability=?,required=?,source_state=?
               WHERE node_id=? AND move_uci=?""",
            (
                normalized_by_move.get(row["move_uci"]),
                int(row["move_uci"] in required),
                source_state,
                node_id,
                row["move_uci"],
            ),
        )


def execute_coverage_node(node: dict) -> None:
    try:
        settings = json.loads(node["settings_json"])
        rating_bucket = _nearest_rating_bucket(int(settings["maia_elo"]))
        speeds = "blitz,rapid,classical"
        ratings = str(rating_bucket)
        with connection() as database:
            payload, cache_key = _cached_explorer_payload(
                database, node["fen"], speeds, ratings
            )
        if payload is None:
            payload = _fetch_explorer(node["fen"], speeds, ratings)
        moves = payload.get("moves", [])
        counts = {
            str(move.get("uci")): int(move.get("white", 0))
            + int(move.get("draws", 0))
            + int(move.get("black", 0))
            for move in moves
            if move.get("uci")
        }
        total_games = sum(counts.values())
        covered_replies = set(json.loads(node["covered_replies_json"]))
        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            database.execute(
                """INSERT INTO explorer_position_cache(
                       cache_key,fen_key,speeds,ratings,response_json,fetched_at
                   ) VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                       response_json=excluded.response_json,fetched_at=excluded.fetched_at""",
                (
                    cache_key,
                    node["fen_key"],
                    speeds,
                    ratings,
                    json.dumps(payload),
                    _now(),
                ),
            )
            for move_uci, move_games in counts.items():
                database.execute(
                    """INSERT INTO repertoire_coverage_candidates(
                           node_id,move_uci,explorer_probability,covered,source_state
                       ) VALUES(?,?,?,?,?) ON CONFLICT(node_id,move_uci) DO UPDATE SET
                           explorer_probability=excluded.explorer_probability,
                           covered=excluded.covered,source_state=excluded.source_state""",
                    (
                        node["id"],
                        move_uci,
                        move_games / total_games if total_games else None,
                        int(move_uci in covered_replies),
                        "explorer-only",
                    ),
                )
            _recalculate_node(database, node["id"], settings)
            database.execute(
                """UPDATE repertoire_coverage_nodes
                   SET explorer_status='complete',explorer_games=?,last_error=NULL,updated_at=?
                   WHERE id=?""",
                (total_games, _now(), node["id"]),
            )
            completed = database.execute(
                "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status='complete'",
                (node["run_id"],),
            ).fetchone()[0]
            remaining = database.execute(
                "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status IN ('queued','running')",
                (node["run_id"],),
            ).fetchone()[0]
            database.execute(
                """UPDATE repertoire_coverage_runs SET completed_nodes=?,status=?,updated_at=?
                   WHERE id=?""",
                (completed, "complete" if not remaining else "running", _now(), node["run_id"]),
            )
    except Exception as error:
        with connection(background=True) as database:
            database.execute(
                "UPDATE repertoire_coverage_nodes SET explorer_status='failed',last_error=?,updated_at=? WHERE id=?",
                (str(error), _now(), node["id"]),
            )
            database.execute(
                "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? WHERE id=?",
                (str(error), _now(), node["run_id"]),
            )


def coverage_summary(repertoire_id: str) -> dict:
    with connection() as database:
        run = database.execute(
            "SELECT * FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1",
            (repertoire_id,),
        ).fetchone()
        if not run:
            return {
                "run_id": None,
                "status": "not-started",
                "required_branches": 0,
                "covered_branches": 0,
                "probability_coverage": None,
                "is_complete": False,
                "unknown_nodes": 0,
            }
        totals = database.execute(
            """SELECT COUNT(*) required_branches,
                      COALESCE(SUM(covered),0) covered_branches,
                      COALESCE(SUM(blended_probability),0) required_probability,
                      COALESCE(SUM(CASE WHEN covered=1 THEN blended_probability ELSE 0 END),0) covered_probability
               FROM repertoire_coverage_candidates c
               JOIN repertoire_coverage_nodes n ON n.id=c.node_id
               WHERE n.run_id=? AND c.required=1""",
            (run["id"],),
        ).fetchone()
        unknown_nodes = database.execute(
            "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status!='complete'",
            (run["id"],),
        ).fetchone()[0]
        required_probability = float(totals["required_probability"] or 0)
        covered_branches = int(totals["covered_branches"] or 0)
        required_branches = int(totals["required_branches"] or 0)
        return {
            "run_id": run["id"],
            "status": run["status"],
            "required_branches": required_branches,
            "covered_branches": covered_branches,
            "probability_coverage": (
                float(totals["covered_probability"] or 0) / required_probability
                if required_probability
                else None
            ),
            "is_complete": bool(
                run["status"] == "complete"
                and not unknown_nodes
                and required_branches > 0
                and covered_branches == required_branches
            ),
            "unknown_nodes": unknown_nodes,
            "last_error": run["last_error"],
            "settings": json.loads(run["settings_json"]),
        }


def coverage_gaps(repertoire_id: str) -> list[dict]:
    with connection() as database:
        run = database.execute(
            "SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1",
            (repertoire_id,),
        ).fetchone()
        if not run:
            return []
        return [
            {
                "gap_id": f"{row['node_id']}:{row['move_uci']}",
                "node_id": row["node_id"],
                "fen": row["fen"],
                "fen_key": row["fen_key"],
                "move_uci": row["move_uci"],
                "probability": row["blended_probability"],
                "source_state": row["source_state"],
                "explorer_games": row["explorer_games"],
                "trained_color": row["trained_color"],
            }
            for row in database.execute(
                """SELECT c.*,n.fen,n.fen_key,n.explorer_games,n.trained_color
                   FROM repertoire_coverage_candidates c
                   JOIN repertoire_coverage_nodes n ON n.id=c.node_id
                   WHERE n.run_id=? AND c.required=1 AND c.covered=0
                   ORDER BY c.blended_probability DESC,c.move_uci""",
                (run["id"],),
            )
        ]


def claim_maia_coverage_node() -> dict | None:
    now = datetime.now(timezone.utc)
    lease_id = str(uuid.uuid4())
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            """UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,lease_expires_at=NULL
               WHERE maia_status='leased' AND lease_expires_at<?""",
            (now.isoformat(),),
        )
        node = database.execute(
            """SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id
               WHERE n.explorer_status='complete' AND n.maia_status='queued'
               ORDER BY r.created_at,n.ply,n.id LIMIT 1"""
        ).fetchone()
        if not node:
            return None
        database.execute(
            "UPDATE repertoire_coverage_nodes SET maia_status='leased',lease_id=?,lease_expires_at=?,updated_at=? WHERE id=?",
            (lease_id, (now + timedelta(minutes=5)).isoformat(), now.isoformat(), node["id"]),
        )
        settings = json.loads(node["settings_json"])
        return {
            "node_id": node["id"],
            "lease_id": lease_id,
            "fen": node["fen"],
            "elo": settings["maia_elo"],
        }


def submit_maia_coverage(node_id: str, lease_id: str, moves: list[dict]) -> None:
    with connection() as database:
        node = database.execute(
            """SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id WHERE n.id=?""",
            (node_id,),
        ).fetchone()
        if not node or node["maia_status"] != "leased" or node["lease_id"] != lease_id:
            raise RuntimeError("Coverage MAIA lease is no longer active")
        covered_replies = set(json.loads(node["covered_replies_json"]))
        for move in moves:
            database.execute(
                """INSERT INTO repertoire_coverage_candidates(
                       node_id,move_uci,maia_probability,covered,source_state
                   ) VALUES(?,?,?,?,?) ON CONFLICT(node_id,move_uci) DO UPDATE SET
                       maia_probability=excluded.maia_probability,
                       covered=excluded.covered""",
                (
                    node_id,
                    move["move_uci"],
                    move["probability"],
                    int(move["move_uci"] in covered_replies),
                    "maia-only",
                ),
            )
        _recalculate_node(database, node_id, json.loads(node["settings_json"]))
        database.execute(
            """UPDATE repertoire_coverage_nodes SET maia_status='complete',lease_id=NULL,
                      lease_expires_at=NULL,updated_at=? WHERE id=?""",
            (_now(), node_id),
        )
