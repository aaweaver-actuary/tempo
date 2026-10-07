"""OF-1/2: real PostgreSQL dismissal persistence and independent source lifecycle."""
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

import chess
from app import postgres_store, coverage_maia_commands
from app.services import repertoire_opportunities as opportunities
from app.services.durable_tasks import complete_task, enqueue_task_in_transaction


def prove_opportunity_dismissal_and_source_freshness(run_bounded_task_slices):
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Opportunity freshness proof requires disposable PostgreSQL")
    repertoire_id = "opportunity-freshness-" + uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    board = chess.Board()
    board.push_uci("e2e4")
    fen_key = " ".join(board.fen().split()[:4])
    old_run, new_run = repertoire_id + "-old", repertoire_id + "-new"
    old_node, new_node = old_run + "-node", new_run + "-node"
    game_ids = [repertoire_id + f"-game-{number}" for number in range(3)]

    def refresh():
        with postgres_store.connection() as database:
            enqueue_task_in_transaction(database, "repertoire_opportunity", repertoire_id,
                {"repertoire_id": repertoire_id, "phase": "nodes", "cursor": ""}, priority=-1000)
        def execute(task):
            if not opportunities.execute_opportunity_slice(task):
                complete_task(task["id"], task["generation"], task["lease_token"], kind=task["kind"])
        # The runner closes pools after every slice, proving restartable publication/cleanup.
        run_bounded_task_slices("repertoire_opportunity", repertoire_id, execute)

    def published():
        with postgres_store.connection(read_only=True) as database:
            return dict(database.execute("SELECT * FROM repertoire_opportunities WHERE repertoire_id=?",
                                         (repertoire_id,)).fetchone())

    try:
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
                             (repertoire_id, repertoire_id, repertoire_id + ".pgn", now.isoformat()))
            database.execute("""INSERT INTO repertoire_lines
                (id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(?,?,'Freshness','white',?,'["e2e4","e7e5","g1f3"]',?)""",
                (repertoire_id + "-line", repertoire_id, chess.STARTING_FEN, now.isoformat()))
            settings = json.dumps({"maia_elo": 1500, "reply_denominator": 20,
                                   "cumulative_target": 0.95, "path_floor": 0.0005})
            for run_id, node_id, created, status, explorer, maia, covered in (
                (old_run, old_node, now-timedelta(days=1), "complete", "complete", "complete", 1),
                (new_run, new_node, now, "failed", "failed", "complete", 0),
            ):
                database.execute("""INSERT INTO repertoire_coverage_runs
                    (id,repertoire_id,status,settings_json,total_nodes,completed_nodes,created_at,updated_at)
                    VALUES(?,?,?,?,1,1,?,?)""", (run_id, repertoire_id, status, settings, created.isoformat(), created.isoformat()))
                database.execute("""INSERT INTO repertoire_coverage_nodes
                    (id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,
                     covered_replies_json,explorer_status,maia_status,explorer_games,updated_at)
                    VALUES(?,?,?,?,?,1,'white','[["e2e4"]]',?,?,?,?,?)""",
                    (node_id,run_id,repertoire_id,board.fen(),fen_key,
                     '["e7e5","c7c5"]' if covered else '["e7e5"]',explorer,maia,500,now.isoformat()))
                database.execute("""INSERT INTO repertoire_coverage_candidates
                    (node_id,move_uci,explorer_probability,maia_probability,covered,source_state)
                    VALUES(?,'c7c5',0.15,0.2,?,'blended')""", (node_id,covered))
        refresh()
        row = published()
        evidence = json.loads(row["evidence_json"])
        assert row["status"] == "active" and evidence["coverage_node_id"] == new_node
        assert evidence["maia_probability"] == 0.2 and evidence["explorer_probability"] is None
        assert evidence["source_provenance"]["maia"]["coverage_run_id"] == new_run
        opportunity_id = row["id"]
        with postgres_store.connection() as database:
            assert opportunities.dismiss_opportunity(database, repertoire_id, opportunity_id)
        dismissal = published()["dismissed_evidence_json"]
        with postgres_store.connection() as database:
            database.execute("UPDATE repertoire_coverage_nodes SET maia_status='failed' WHERE id=?", (new_node,))
        refresh()
        assert published()["status"] == "resolved"
        assert published()["dismissed_evidence_json"] == dismissal
        # Symmetric source recovery and changed probability/provenance are not material games.
        with postgres_store.connection() as database:
            database.execute("UPDATE repertoire_coverage_nodes SET explorer_status='complete' WHERE id=?", (new_node,))
        refresh()
        row = published()
        evidence = json.loads(row["evidence_json"])
        assert row["status"] == "dismissed" and row["dismissed_evidence_json"] == dismissal
        assert evidence["explorer_probability"] == 0.15 and evidence["maia_probability"] is None
        assert evidence["source_provenance"]["explorer"]["coverage_node_id"] == new_node
        # Staleness and restoration preserve the exact dismissal across fresh processes.
        with postgres_store.connection() as database:
            database.execute("UPDATE repertoire_coverage_runs SET created_at=? WHERE id=?",
                             ((now-timedelta(days=8)).isoformat(), new_run))
            database.execute("UPDATE repertoire_coverage_runs SET created_at=? WHERE id=?",
                             ((now-timedelta(days=10)).isoformat(), old_run))
        refresh()
        assert published()["status"] == "resolved"
        with postgres_store.connection() as database:
            database.execute("UPDATE repertoire_coverage_runs SET created_at=? WHERE id=?", (now.isoformat(),new_run))
        refresh()
        refresh()
        assert published()["status"] == "dismissed" and published()["dismissed_evidence_json"] == dismissal
        with postgres_store.connection() as database:
            for game_id in game_ids:
                database.execute("""INSERT INTO imported_games
                    (id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                    VALUES(?,'lichess','freshness-player',?,'rapid',1,'white','*',?,'["e2e4","c7c5"]')""",
                    (game_id,now.isoformat(),chess.STARTING_FEN))
                database.execute("""INSERT INTO game_repertoire_matches
                    (game_id,repertoire_id,classification,updated_at) VALUES(?,?,'opponent repertoire gap',?)""",
                    (game_id,repertoire_id,now.isoformat()))
                database.execute("INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci) VALUES(?,1,?,'c7c5')",
                                 (game_id,fen_key))
        refresh()
        row = published()
        assert row["id"] == opportunity_id and row["status"] == "active"
        assert row["dismissed_evidence_json"] is None
        refresh()
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM repertoire_opportunities WHERE repertoire_id=?", (repertoire_id,)).fetchone()[0] == 1
            assert database.execute("SELECT COUNT(*) FROM cards WHERE repertoire_id=?", (repertoire_id,)).fetchone()[0] == 0
            assert database.execute("SELECT moves_json FROM repertoire_lines WHERE repertoire_id=?", (repertoire_id,)).fetchone()[0] == '["e2e4","e7e5","g1f3"]'
        print("PASS OF-1 PostgreSQL dismissal survives unavailable/stale resolution and replay; only three new games reopen; no cards or repertoire mutation", flush=True)
        # Real lease callback remains usable after the other source makes the run failed.
        with postgres_store.connection() as database:
            database.execute("UPDATE repertoire_coverage_nodes SET explorer_status='failed',maia_status='leased',lease_id='of-lease' WHERE id=?", (new_node,))
            database.execute("UPDATE repertoire_coverage_runs SET status='failed' WHERE id=?", (new_run,))
            coverage_maia_commands.submit_maia_node(database, {"node_id":new_node,"lease_id":"of-lease",
                "moves":[{"move_uci":"c7c5","probability":0.3}]})
        refresh()
        assert json.loads(published()["evidence_json"])["maia_probability"] == 0.3
        print("PASS OF-2 PostgreSQL newest partial Maia/Explorer evidence, exact provenance, cleanup, durable callback and source-status replay", flush=True)
    finally:
        with postgres_store.connection() as database:
            database.execute_native("DELETE FROM imported_games WHERE id=ANY(%s::text[])", (game_ids,))
            database.execute_native("DELETE FROM background_tasks WHERE deduplication_key=%s OR payload_json::jsonb->>'repertoire_id'=%s OR payload_json::jsonb->>'opportunity_id' IN (SELECT id FROM repertoire_opportunities WHERE repertoire_id=%s)", (repertoire_id,repertoire_id,repertoire_id))
            database.execute("DELETE FROM repertoires WHERE id=?", (repertoire_id,))
        postgres_store.close_pools()
