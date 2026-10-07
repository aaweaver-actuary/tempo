"""Shared SQLite/PostgreSQL acceptance proof for issue 12; no application mocks."""

from dataclasses import asdict
from datetime import datetime, timezone
import json

from app import database, postgres_store
from app.services.durable_tasks import enqueue_task_in_transaction
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine
from app.services import threat_pipeline
from app.services.threat_validation import AnalysisLine, AnalysisReport, EngineScore


def verify_three_anchor_persistence(*, reconnect=lambda: None):
    start_fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    moves = ("a2a3", "e8e7", "a3a4", "e7e8", "a4a5", "b4c2")
    game = GameSnapshot("lichess:issue12-three-anchors", 1, start_fen, moves, "white")
    seed, = find_defensive_knight_forks(game, SourceLine("played", 0, moves))
    route, anchors, _plans = threat_pipeline._prepare_seed(game, seed)
    assert [(anchor.player_ply, anchor.historical_move_uci) for anchor in anchors] == [
        (4, "a4a5"), (2, "a3a4"), (0, "a2a3"),
    ]
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute(
            "UPDATE settings SET defensive_analysis_enabled=1,defense_new_cards_per_day=0 WHERE id=1"
        )
        connection.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                   result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','issue12',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game.game_id, now, start_fen, json.dumps(moves)),
        )
        connection.execute(
            """INSERT INTO repertoires(id,name,source_name,created_at)
               VALUES('issue12-existing-study','Existing study','regression',?)""", (now,),
        )
        connection.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                   interval_days,repetitions,stability,fsrs_card_json)
               VALUES('issue12-existing-card','issue12-existing-study','prefix',?,'["a2a3"]',
                   'learning','2026-01-01',7,2,3.5,'{"existing_memory":true}')""", (start_fen,),
        )

    def memory_and_offense_snapshot():
        with database.connection() as connection:
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
                    for table in ("cards", "reviews", "daily_queue", "tactical_opportunities", "gameplay_events")}

    before = memory_and_offense_snapshot()

    def persist_and_validate():
        with database.connection() as connection:
            threat_pipeline._upsert_seed(connection, game, seed)
            candidates = [dict(row) for row in connection.execute(
                "SELECT * FROM threat_training_candidates WHERE game_id=? ORDER BY player_ply",
                (game.game_id,),
            )]
            for candidate in candidates:
                anchor_ply = candidate["player_ply"]
                for relation in connection.execute(
                    """SELECT relation.role,request.id,request.request_json
                       FROM threat_candidate_requests relation
                       JOIN threat_analysis_requests request ON request.id=relation.request_id
                       WHERE relation.candidate_id=?""", (candidate["id"],),
                ).fetchall():
                    request = threat_pipeline._request_from_json(json.loads(relation["request_json"]))
                    if relation["role"] == "historical":
                        variation = (*moves[anchor_ply:], "e1d2", "c2a1", "d2c1")
                        score = EngineScore(cp=-180)
                    else:
                        variation = ("e1f2", "e7e8" if anchor_ply == 2 else "e8e7")
                        score = EngineScore(cp=0)
                    report = AnalysisReport(request, (
                        AnalysisLine(variation[0], variation, score, request.depth),
                    ), True)
                    threat_pipeline.validate_analysis_report(request, report)
                    connection.execute(
                        "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
                        (json.dumps(asdict(report)), relation["id"]),
                    )
        for candidate in candidates:
            task = {"payload": {"candidate_id": candidate["id"]}}
            if postgres_store.configured():
                with database.connection() as connection:
                    task = enqueue_task_in_transaction(
                        connection, "defensive_threat_validate", candidate["id"], task["payload"],
                    )
                    task["payload"] = json.loads(task["payload_json"])
                    task["lease_token"] = "issue12-validation-lease"
                    connection.execute(
                        "UPDATE background_tasks SET state='leased',lease_token=? WHERE id=?",
                        (task["lease_token"], task["id"]),
                    )
            threat_pipeline.execute_threat_validation(task)

    def assert_durable_evidence():
        with database.connection() as connection:
            candidates = [dict(row) for row in connection.execute(
                "SELECT * FROM threat_training_candidates WHERE game_id=? ORDER BY player_ply",
                (game.game_id,),
            )]
            assert len(candidates) == 3
            assert len({candidate["id"] for candidate in candidates}) == 3
            assert len({candidate["incident_id"] for candidate in candidates}) == 1
            assert len({candidate["finding_id"] for candidate in candidates}) == 1
            finding, = connection.execute(
                "SELECT * FROM game_findings WHERE game_id=?", (game.game_id,),
            ).fetchall()
            assert finding["id"] == candidates[0]["finding_id"]
            assert (finding["kind"], finding["motif"]) == ("defensive tactical threat", "fork")
            finding_evidence = json.loads(finding["evidence_json"])
            assert finding_evidence["incident_id"] == candidates[0]["incident_id"]
            assert finding_evidence["seed"] == json.loads(json.dumps(asdict(seed)))
            # Recurrence's durable units are incidents and supporting games, never anchor rows.
            assert tuple(connection.execute(
                """SELECT COUNT(DISTINCT incident_id),COUNT(DISTINCT game_id)
                   FROM threat_training_candidates WHERE game_id=? AND superseded_at IS NULL""",
                (game.game_id,),
            ).fetchone()) == (1, 1)
            expected_anchors = {anchor.player_ply: anchor for anchor in anchors}
            for candidate in candidates:
                evidence = json.loads(candidate["evidence_json"])
                assert evidence == json.loads(json.dumps({
                    "seed": asdict(seed), "anchor": asdict(expected_anchors[candidate["player_ply"]]),
                    "knight_route": [asdict(hop) for hop in route],
                }))
                assert candidate["source_fingerprint"] == threat_pipeline._digest(evidence)
                assert candidate["detector_version"] == threat_pipeline.DETECTOR_VERSION
                assert json.loads(candidate["policy_json"]) == asdict(threat_pipeline.POLICY)
                assert candidate["analysis_version"] == game.analysis_version
                assert candidate["exercise_revision"] == 1
                assert candidate["card_id"] is None and candidate["approved_at"] is None
                assert candidate["superseded_at"] is None
                validation = json.loads(candidate["validation_json"])
                assert candidate["validation_state"] == (
                    "engine_supported" if candidate["player_ply"] == 4 else "lesson_only"
                )
                reports = [threat_pipeline.report_from_json(json.loads(row["report_json"]))
                           for row in connection.execute(
                               """SELECT request.report_json FROM threat_candidate_requests relation
                                  JOIN threat_analysis_requests request ON request.id=relation.request_id
                                  WHERE relation.candidate_id=?""", (candidate["id"],),
                           )]
                assert len(reports) == 2
                assert set(validation["report_ids"]) == {report.report_id for report in reports}
            return [(candidate["id"], candidate["finding_id"], candidate["incident_id"],
                     candidate["source_fingerprint"], candidate["validation_json"])
                    for candidate in candidates]

    persist_and_validate()
    reconnect()
    identities = assert_durable_evidence()
    assert memory_and_offense_snapshot() == before
    persist_and_validate()
    reconnect()
    assert assert_durable_evidence() == identities
    assert memory_and_offense_snapshot() == before
