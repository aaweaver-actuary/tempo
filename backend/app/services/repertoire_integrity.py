"""Position-level integrity checks and repairs for opening repertoires."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import sqlite3
import uuid

import chess

from .cards import card_id
from .repertoire_comparison import canonical_fen

SYSTEM_REPERTOIRES = {"__tactics__", "__endgames__", "__game_mistakes__"}
_LOGGER = logging.getLogger("tempo.background")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_id(repertoire_id: str, kind: str, fen_key: str | None, sources: list[dict] | None = None) -> str:
    source_key = "|".join(
        f"{source.get('type')}:{source.get('id')}"
        for source in (sources or [])
    )
    return hashlib.sha256(
        f"{repertoire_id}\0{kind}\0{fen_key or source_key}".encode()
    ).hexdigest()


def _signature(issue: dict) -> str:
    payload = {
        "kind": issue["kind"],
        "fen_key": issue.get("fen_key"),
        "moves": sorted(issue.get("moves", [])),
        "sources": sorted(
            [
                (source.get("type"), source.get("id"), source.get("move_index"), source.get("move"))
                for source in issue.get("sources", [])
            ],
            key=repr,
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _source_rows(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    lines = [
        {**dict(row), "source_type": "line", "source_id": row["id"]}
        for row in database.execute(
            "SELECT id,repertoire_id,name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
            (repertoire_id,),
        ).fetchall()
    ]
    cards = [
        {**dict(row), "source_type": "card", "source_id": row["id"]}
        for row in database.execute(
        """SELECT DISTINCT c.id,c.repertoire_id,c.kind,c.start_fen,c.moves_json,
                              COALESCE(c.trained_color,(
                                  SELECT l.trained_color FROM repertoire_lines l
                                  WHERE l.repertoire_id=? ORDER BY l.created_at,l.id LIMIT 1
                              )) trained_color
               FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
               WHERE (rc.repertoire_id=? OR c.repertoire_id=?) AND c.archived=0 AND c.content_type='opening'
               ORDER BY c.id""",
            (repertoire_id, repertoire_id, repertoire_id),
        ).fetchall()
    ]
    return lines + cards


def _scan_source(
    source: dict, repertoire_color: str | None
) -> tuple[dict[str, dict], list[dict]]:
    positions: dict[str, dict] = {}
    invalid: list[dict] = []
    source_label = f"{source['source_type']} {source['source_id']}"
    try:
        board = chess.Board(source["start_fen"])
        moves = json.loads(source["moves_json"])
        if not isinstance(moves, list):
            raise ValueError("moves_json must be a list")
        target = chess.WHITE if source["trained_color"] == "white" else chess.BLACK
        if source["trained_color"] not in {"white", "black"}:
            raise ValueError("trained color is unknown")
        if repertoire_color is not None and source["trained_color"] != repertoire_color:
            raise ValueError(
                f"trained color {source['trained_color']} does not match repertoire color {repertoire_color}"
            )
        last_moving_color = None
        for move_index, move_value in enumerate(moves):
            if board.turn == target:
                _record_position(positions, board, source, move_index, str(move_value))
            move = chess.Move.from_uci(str(move_value).lower())
            if move not in board.legal_moves:
                raise ValueError(f"illegal move at ply {move_index + 1}")
            last_moving_color = board.turn
            board.push(move)
        if board.turn == target and any(board.legal_moves) and last_moving_color != target:
            _record_position(positions, board, source, len(moves), None)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as error:
        invalid.append(
            {
                "kind": "invalid_source",
                "fen_key": None,
                "fen": None,
                "trained_color": source.get("trained_color"),
                "moves": [],
                "sources": [
                    {
                        "type": source["source_type"],
                        "id": source["source_id"],
                        "label": source_label,
                        "error": str(error),
                    }
                ],
            }
        )
    return positions, invalid


def _scan_sources(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    positions: dict[str, dict] = {}
    invalid: list[dict] = []
    repertoire_color: str | None = None
    for source in _source_rows(database, repertoire_id):
        source_positions, source_invalid = _scan_source(source, repertoire_color)
        if repertoire_color is None and source.get("trained_color") in {"white", "black"}:
            repertoire_color = source["trained_color"]
        for key, value in source_positions.items():
            if key not in positions:
                positions[key] = value
            else:
                positions[key]["moves"].extend(value["moves"])
                positions[key]["sources"].extend(value["sources"])
        invalid.extend(source_invalid)
    issues: list[dict] = []
    for value in positions.values():
        move_set = set(value["moves"])
        if len(move_set) == 1:
            continue
        value["kind"] = "missing_response" if not move_set else "multiple_responses"
        issues.append(value)
    for issue in invalid:
        issue["id"] = _issue_id(repertoire_id, issue["kind"], None, issue.get("sources"))
        issue["signature"] = _signature(issue)
        issues.append(issue)
    for issue in issues:
        issue.setdefault("id", _issue_id(repertoire_id, issue["kind"], issue.get("fen_key"), issue.get("sources")))
        issue.setdefault("signature", _signature(issue))
    return sorted(
        issues,
        key=lambda item: (item.get("fen_key") or "", item["kind"], item["id"]),
    )


def _record_position(
    positions: dict[str, dict],
    board: chess.Board,
    source: dict,
    move_index: int,
    move: str | None,
) -> None:
    fen_key = canonical_fen(board.fen())
    entry = positions.setdefault(
        fen_key,
        {
            "fen_key": fen_key,
            "fen": board.fen(),
            "trained_color": source["trained_color"],
            "moves": [],
            "sources": [],
        },
    )
    if move is not None:
        entry["moves"].append(move.lower())
    entry["sources"].append(
        {
            "type": source["source_type"],
            "id": source["source_id"],
            "move_index": move_index,
            "move": move.lower() if move else None,
        }
    )


def sweep_repertoire(database: sqlite3.Connection, repertoire_id: str) -> dict:
    if not database.execute(
        "SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)
    ).fetchone():
        raise KeyError("Repertoire not found")
    issues = _scan_sources(database, repertoire_id)
    now = _now()
    database.execute(
        "DELETE FROM repertoire_integrity_issues WHERE repertoire_id=?",
        (repertoire_id,),
    )
    for issue in issues:
        database.execute(
            """INSERT INTO repertoire_integrity_issues(
                       id,repertoire_id,kind,fen_key,fen,trained_color,signature,
                       moves_json,sources_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                issue["id"],
                repertoire_id,
                issue["kind"],
                issue.get("fen_key"),
                issue.get("fen"),
                issue.get("trained_color"),
                issue["signature"],
                json.dumps(sorted(set(issue.get("moves", [])))),
                json.dumps(issue.get("sources", [])),
                now,
                now,
            ),
        )
    status = "needs_repair" if issues else "clean"
    database.execute(
        """INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at)
           VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at""",
        (repertoire_id, status, now),
    )
    if status != "clean":
        database.execute(
            "DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?",
            (repertoire_id,),
        )
    return integrity_summary(database, repertoire_id)


def sweep_all(database: sqlite3.Connection) -> None:
    ids = [
        row[0]
        for row in database.execute("SELECT id FROM repertoires").fetchall()
        if row[0] not in SYSTEM_REPERTOIRES
    ]
    for repertoire_id in ids:
        sweep_repertoire(database, repertoire_id)


def enqueue_integrity_scans(
    repertoire_id: str | None = None, *, stale_only: bool = False
) -> list[str]:
    """Queue integrity work without changing the last published result."""

    queued: list[str] = []
    from ..database import connection

    with connection() as database:
        if repertoire_id:
            ids = [repertoire_id]
        elif stale_only:
            ids = [
                row[0]
                for row in database.execute(
                    """SELECT r.id FROM repertoires r
                       LEFT JOIN repertoire_integrity_state s ON s.repertoire_id=r.id
                       WHERE r.id NOT IN (?,?,?)
                         AND (s.repertoire_id IS NULL OR s.status='unchecked' OR s.scan_status='failed')
                       ORDER BY r.id""",
                    tuple(SYSTEM_REPERTOIRES),
                )
            ]
        else:
            ids = [
                row[0]
                for row in database.execute(
                    "SELECT id FROM repertoires WHERE id NOT IN (?,?,?) ORDER BY id",
                    tuple(SYSTEM_REPERTOIRES),
                )
            ]
        now = _now()
        for identifier in ids:
            if not database.execute(
                "SELECT 1 FROM repertoires WHERE id=?", (identifier,)
            ).fetchone():
                raise KeyError("Repertoire not found")
            active = database.execute(
                "SELECT run_id,status FROM repertoire_integrity_jobs WHERE repertoire_id=? AND status IN ('queued','running','finalizing')",
                (identifier,),
            ).fetchone()
            if active:
                queued.append(active["run_id"])
                continue
            total = database.execute(
                "SELECT COUNT(*) FROM (SELECT id FROM repertoire_lines WHERE repertoire_id=? UNION ALL SELECT DISTINCT c.id FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id WHERE (rc.repertoire_id=? OR c.repertoire_id=?) AND c.archived=0 AND c.content_type='opening')",
                (identifier, identifier, identifier),
            ).fetchone()[0]
            run_id = str(uuid.uuid4())
            database.execute(
                "INSERT INTO repertoire_integrity_jobs(repertoire_id,run_id,status,source_offset,total_sources,updated_at) VALUES(?,?, 'queued',0,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET run_id=excluded.run_id,status='queued',source_offset=0,total_sources=excluded.total_sources,attempts=0,last_error=NULL,updated_at=excluded.updated_at",
                (identifier, run_id, total, now),
            )
            database.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,scan_completed_sources,scan_total_sources,scan_error) VALUES(?, 'unchecked','queued',NULL,0,?,NULL) ON CONFLICT(repertoire_id) DO UPDATE SET scan_status='queued',scan_generation=excluded.scan_generation,scan_completed_sources=0,scan_total_sources=excluded.scan_total_sources,scan_error=NULL",
                (identifier, total),
            )
            queued.append(run_id)
    return queued


def _issues_from_observations(
    repertoire_id: str, observations: list[dict], invalid: list[dict]
) -> list[dict]:
    positions: dict[str, dict] = {}
    for source_positions in observations:
        for value in source_positions:
            key = value["fen_key"]
            if key not in positions:
                positions[key] = value
            else:
                positions[key]["moves"].extend(value.get("moves", []))
                positions[key]["sources"].extend(value.get("sources", []))
    issues: list[dict] = []
    for value in positions.values():
        move_set = set(value["moves"])
        if len(move_set) == 1:
            continue
        value["kind"] = "missing_response" if not move_set else "multiple_responses"
        issues.append(value)
    for issue in invalid:
        issue["id"] = _issue_id(repertoire_id, issue["kind"], None, issue.get("sources"))
        issue["signature"] = _signature(issue)
        issues.append(issue)
    for issue in issues:
        issue.setdefault("id", _issue_id(repertoire_id, issue["kind"], issue.get("fen_key"), issue.get("sources")))
        issue.setdefault("signature", _signature(issue))
    return sorted(issues, key=lambda item: (item.get("fen_key") or "", item["kind"], item["id"]))


def claim_integrity_slice() -> dict | None:
    from ..database import connection

    with connection(background=True) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT * FROM repertoire_integrity_jobs WHERE status IN ('queued','running','finalizing') ORDER BY updated_at,repertoire_id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        now = _now()
        if row["status"] == "finalizing" or row["source_offset"] >= row["total_sources"]:
            database.execute(
                "UPDATE repertoire_integrity_jobs SET status='finalizing',updated_at=? WHERE repertoire_id=?",
                (now, row["repertoire_id"]),
            )
            database.execute(
                "UPDATE repertoire_integrity_state SET scan_status='running' WHERE repertoire_id=?",
                (row["repertoire_id"],),
            )
            return {**dict(row), "status": "finalizing"}
        database.execute(
            "UPDATE repertoire_integrity_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE repertoire_id=?",
            (now, row["repertoire_id"]),
        )
        database.execute(
            "UPDATE repertoire_integrity_state SET scan_status='running' WHERE repertoire_id=?",
            (row["repertoire_id"],),
        )
        return {**dict(row), "status": "running"}


def execute_integrity_slice(job: dict) -> None:
    from ..database import connection
    from .activity_gate import activity_gate

    with activity_gate.background_job("integrity", job["repertoire_id"]):
        if job["status"] == "finalizing":
            with connection(background=True) as database:
                staged = database.execute(
                    "SELECT observations_json,invalid_json FROM repertoire_integrity_source_runs WHERE run_id=? ORDER BY source_offset",
                    (job["run_id"],),
                ).fetchall()
            observations = [json.loads(row[0]) for row in staged]
            invalid = [item for row in staged for item in json.loads(row[1])]
            issues = _issues_from_observations(job["repertoire_id"], observations, invalid)
            now = _now()
            with connection(background=True) as database:
                database.execute("DELETE FROM repertoire_integrity_issues WHERE repertoire_id=?", (job["repertoire_id"],))
                for issue in issues:
                    database.execute(
                        "INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (issue["id"], job["repertoire_id"], issue["kind"], issue.get("fen_key"), issue.get("fen"), issue.get("trained_color"), issue["signature"], json.dumps(sorted(set(issue.get("moves", [])))), json.dumps(issue.get("sources", [])), now, now),
                    )
                status = "needs_repair" if issues else "clean"
                database.execute(
                    "UPDATE repertoire_integrity_state SET status=?,checked_at=?,scan_status='idle',scan_generation=?,scan_completed_sources=scan_total_sources,scan_error=NULL WHERE repertoire_id=?",
                    (status, now, job["run_id"], job["repertoire_id"]),
                )
                if status == "clean":
                    database.execute(
                        """UPDATE cards SET pending_validation=0,state=CASE WHEN state='locked' THEN 'new' ELSE state END
                           WHERE pending_validation=1 AND archived=0 AND
                             (repertoire_id=? OR id IN (SELECT card_id FROM repertoire_cards WHERE repertoire_id=?))""",
                        (job["repertoire_id"], job["repertoire_id"]),
                    )
                database.execute(
                    "UPDATE repertoire_integrity_jobs SET status='complete',last_error=NULL,updated_at=? WHERE repertoire_id=?",
                    (now, job["repertoire_id"]),
                )
            from .database_executor import database_writer
            from .durable_tasks import enqueue_task

            if database_writer.healthy:
                enqueue_task(
                    "daily_queue",
                    "current",
                    {},
                    priority=10,
                    foreground=False,
                )
            return

        with connection(background=True) as database:
            sources = _source_rows(database, job["repertoire_id"])
            source = sources[job["source_offset"]]
            repertoire_color = next((item.get("trained_color") for item in sources if item.get("trained_color") in {"white", "black"}), None)
        positions, invalid = _scan_source(source, repertoire_color)
        now = _now()
        with connection(background=True) as database:
            database.execute(
                "INSERT OR REPLACE INTO repertoire_integrity_source_runs(run_id,source_offset,observations_json,invalid_json) VALUES(?,?,?,?)",
                (job["run_id"], job["source_offset"], json.dumps(list(positions.values())), json.dumps(invalid)),
            )
            next_offset = job["source_offset"] + 1
            status = "finalizing" if next_offset >= job["total_sources"] else "running"
            database.execute(
                "UPDATE repertoire_integrity_jobs SET status=?,source_offset=?,updated_at=? WHERE repertoire_id=? AND run_id=?",
                (status, next_offset, now, job["repertoire_id"], job["run_id"]),
            )
            database.execute(
                "UPDATE repertoire_integrity_state SET scan_status='running',scan_completed_sources=?,scan_total_sources=? WHERE repertoire_id=?",
                (next_offset, job["total_sources"], job["repertoire_id"]),
            )


def requeue_integrity_slice(job: dict, error: Exception) -> None:
    """Return a failed slice to durable storage without disturbing published issues."""

    from ..database import connection

    message = str(error)
    now = _now()
    with connection(background=True) as database:
        attempts = database.execute(
            "SELECT attempts FROM repertoire_integrity_jobs WHERE repertoire_id=? AND run_id=?",
            (job["repertoire_id"], job["run_id"]),
        ).fetchone()
        attempt_count = int(attempts[0]) if attempts else int(job.get("attempts") or 0)
        if attempt_count >= 5:
            database.execute(
                "UPDATE repertoire_integrity_jobs SET status='failed',last_error=?,updated_at=? WHERE repertoire_id=? AND run_id=?",
                (message, now, job["repertoire_id"], job["run_id"]),
            )
            database.execute(
                "UPDATE repertoire_integrity_state SET scan_status='failed',scan_error=? WHERE repertoire_id=?",
                (message, job["repertoire_id"]),
            )
        else:
            database.execute(
                "UPDATE repertoire_integrity_jobs SET status='queued',last_error=?,updated_at=? WHERE repertoire_id=? AND run_id=?",
                (message, now, job["repertoire_id"], job["run_id"]),
            )
            database.execute(
                "UPDATE repertoire_integrity_state SET scan_status='retrying',scan_error=? WHERE repertoire_id=?",
                (message, job["repertoire_id"]),
            )
    _LOGGER.warning(
        "integrity slice requeued job_type=integrity job_id=%s retry=%s error=%s",
        job["repertoire_id"],
        attempt_count,
        message,
    )


def integrity_summary(database: sqlite3.Connection, repertoire_id: str) -> dict:
    row = database.execute(
        """SELECT r.id,COALESCE(s.status,'unchecked') integrity_status,s.checked_at integrity_checked_at,
                  COALESCE(s.scan_status,'idle') scan_status,s.scan_generation,
                  COALESCE(s.scan_completed_sources,0) scan_completed_sources,
                  COALESCE(s.scan_total_sources,0) scan_total_sources,s.scan_error
           FROM repertoires r LEFT JOIN repertoire_integrity_state s ON s.repertoire_id=r.id
           WHERE r.id=?""",
        (repertoire_id,),
    ).fetchone()
    if not row:
        raise KeyError("Repertoire not found")
    issue = database.execute(
        "SELECT id FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id LIMIT 1",
        (repertoire_id,),
    ).fetchone()
    count = database.execute(
        "SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id=?",
        (repertoire_id,),
    ).fetchone()[0]
    return {
        "status": row["integrity_status"],
        "issue_count": count,
        "first_issue_id": issue[0] if issue else None,
        "checked_at": row["integrity_checked_at"],
        "scan_status": row["scan_status"],
        "scan_generation": row["scan_generation"],
        "scan_progress": {
            "completed": row["scan_completed_sources"],
            "total": row["scan_total_sources"],
        },
        "last_scan_error": row["scan_error"],
    }


def list_integrity_issues(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    rows = database.execute(
        """SELECT id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json
           FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id""",
        (repertoire_id,),
    ).fetchall()
    results = []
    for row in rows:
        sources = json.loads(row["sources_json"])
        moves = json.loads(row["moves_json"])
        stats = {
            move: {"uci": move, "line_count": 0, "card_count": 0, "review_count": 0}
            for move in moves
        }
        for source in sources:
            move = source.get("move")
            if move not in stats:
                continue
            bucket = stats[move]
            if source.get("type") == "line":
                bucket["line_count"] += 1
            elif source.get("type") == "card":
                bucket["card_count"] += 1
                bucket["review_count"] += database.execute(
                    "SELECT COUNT(*) FROM reviews WHERE card_id=?", (source["id"],)
                ).fetchone()[0]
        results.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "fen_key": row["fen_key"],
                "fen": row["fen"],
                "trained_color": row["trained_color"],
                "signature": row["signature"],
                "moves": list(stats.values()),
                "sources": sources,
            }
        )
    return results


def _transform_source(start_fen: str, moves: list[str], trained_color: str, target_fen: str, selected: str) -> tuple[list[str], bool]:
    board = chess.Board(start_fen)
    target = chess.WHITE if trained_color == "white" else chess.BLACK
    transformed = list(moves)
    for index, move_value in enumerate(list(moves)):
        if board.turn == target and canonical_fen(board.fen()) == target_fen:
            if move_value.lower() == selected:
                return transformed, False
            transformed = transformed[:index] + [selected]
            return transformed, True
        board.push_uci(move_value)
    if board.turn == target and canonical_fen(board.fen()) == target_fen:
        transformed.append(selected)
        return transformed, True
    return transformed, False


def _rewrite_line(database: sqlite3.Connection, row: sqlite3.Row, moves: list[str]) -> bool:
    new_id = hashlib.sha256(
        f"{row['repertoire_id']}\0{card_id(row['start_fen'], moves)}".encode()
    ).hexdigest()
    if new_id == row["id"]:
        return False
    database.execute(
        """INSERT OR IGNORE INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (new_id, row["repertoire_id"], row["name"], row["trained_color"], row["start_fen"], json.dumps(moves), row["created_at"]),
    )
    database.execute("DELETE FROM repertoire_lines WHERE id=?", (row["id"],))
    return True


def _rewrite_card(database: sqlite3.Connection, repertoire_id: str, row: sqlite3.Row, moves: list[str]) -> bool:
    new_id = card_id(row["start_fen"], moves)
    if new_id == row["id"]:
        return False
    existing = database.execute("SELECT 1 FROM cards WHERE id=?", (new_id,)).fetchone()
    if not existing:
        columns = [item[1] for item in database.execute("PRAGMA table_info(cards)")]
        values = dict(row)
        values.update({"id": new_id, "moves_json": json.dumps(moves), "state": "new", "due_date": datetime.now(timezone.utc).date().isoformat(), "interval_days": 0, "repetitions": 0, "lapses": 0, "introduced_at": None, "archived": 0, "superseded_by": None})
        database.execute(
            f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(values.get(column) for column in columns),
        )
    database.execute("INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (repertoire_id, new_id))
    database.execute("DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?", (repertoire_id, row["id"]))
    remaining = database.execute("SELECT 1 FROM repertoire_cards WHERE card_id=? LIMIT 1", (row["id"],)).fetchone()
    if not remaining:
        database.execute("UPDATE cards SET archived=1,superseded_by=? WHERE id=?", (new_id, row["id"]))
    return True


def _reconcile_derived_cards(database: sqlite3.Connection, repertoire_id: str) -> int:
    """Remove opening cards whose route is no longer supported by a retained line."""
    lines = database.execute(
        "SELECT start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=?",
        (repertoire_id,),
    ).fetchall()
    line_routes = [
        (canonical_fen(row["start_fen"]), json.loads(row["moves_json"]))
        for row in lines
    ]
    cards = database.execute(
        """SELECT DISTINCT c.* FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
           WHERE c.content_type='opening' AND c.archived=0
             AND (c.repertoire_id=? OR rc.repertoire_id=?)""",
        (repertoire_id, repertoire_id),
    ).fetchall()
    changed = 0
    for card in cards:
        supported = any(
            canonical_fen(card["start_fen"]) == start_key
            and route[: len(json.loads(card["moves_json"]))] == json.loads(card["moves_json"])
            for start_key, route in line_routes
        )
        if supported:
            continue
        database.execute(
            "DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
            (repertoire_id, card["id"]),
        )
        remaining = database.execute(
            "SELECT 1 FROM repertoire_cards WHERE card_id=? LIMIT 1", (card["id"],)
        ).fetchone()
        if remaining:
            if card["repertoire_id"] == repertoire_id:
                database.execute(
                    "UPDATE cards SET repertoire_id=? WHERE id=?",
                    (remaining[0], card["id"]),
                )
        else:
            database.execute(
                "UPDATE cards SET archived=1,state='locked',superseded_by=NULL WHERE id=?",
                (card["id"],),
            )
        changed += 1
    return changed


def resolve_issue(database: sqlite3.Connection, repertoire_id: str, issue_id: str, signature: str, selected_move: str) -> dict:
    row = database.execute(
        "SELECT * FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
        (issue_id, repertoire_id),
    ).fetchone()
    if not row:
        raise KeyError("Integrity issue not found")
    if row["signature"] != signature:
        raise RuntimeError("This integrity issue changed; refresh and try again")
    if not row["fen"]:
        raise ValueError("Invalid source issues must be repaired by editing or removing the source")
    board = chess.Board(row["fen"])
    selected = selected_move.lower()
    try:
        move = chess.Move.from_uci(selected)
    except ValueError as error:
        raise ValueError("Selected move is not valid UCI") from error
    if move not in board.legal_moves:
        raise ValueError("Selected move is illegal from this position")
    changed_lines = changed_cards = 0
    target_fen = row["fen_key"]
    for source in json.loads(row["sources_json"]):
        if source.get("type") == "line":
            source_row = database.execute("SELECT * FROM repertoire_lines WHERE id=?", (source["id"],)).fetchone()
            if not source_row:
                continue
            moves, changed = _transform_source(source_row["start_fen"], json.loads(source_row["moves_json"]), source_row["trained_color"], target_fen, selected)
            if changed:
                changed_lines += int(_rewrite_line(database, source_row, moves))
        elif source.get("type") == "card":
            source_row = database.execute("SELECT * FROM cards WHERE id=?", (source["id"],)).fetchone()
            if not source_row:
                continue
            trained = source_row["trained_color"] or row["trained_color"]
            moves, changed = _transform_source(source_row["start_fen"], json.loads(source_row["moves_json"]), trained, target_fen, selected)
            if changed:
                changed_cards += int(_rewrite_card(database, repertoire_id, source_row, moves))
    changed_cards += _reconcile_derived_cards(database, repertoire_id)
    summary = sweep_repertoire(database, repertoire_id)
    return {"summary": summary, "changed_line_count": changed_lines, "changed_card_count": changed_cards}
