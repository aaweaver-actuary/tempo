"""Task-owned R11 source/history fixture; every qualifying success is real."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import os
import re
import sys

import chess
import psycopg

from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory

def seed_current_owner(database):
    import chess
    from tempo_evidence_math import opening_position_key
    now = datetime.now().astimezone()
    stamp, day = now.isoformat(),now.date().isoformat()
    route = SimpleNamespace(route_id="r11-route",repertoire_id="r11-repertoire",starting_fen=chess.STARTING_FEN,moves=("e2e4","e7e5","g1f3","b8c6","f1b5"))
    database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'R11 fixture','fixture',%s)", (route.repertoire_id,stamp))
    database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Route','white',%s,%s,%s)",
        (route.route_id,route.repertoire_id,route.starting_fen,json.dumps(route.moves),stamp))
    database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)",(route.repertoire_id,stamp))
    board = chess.Board()
    for index,move in enumerate(route.moves):
        if index%2==0:
            identifier = ("A","B","C")[index//2]
            start = board.fen(en_passant="fen")
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                introduced_at,trained_color,canonical_route_source) VALUES(%s,%s,'response',%s,%s,'learning',%s,%s,'white',0)""",
                (identifier,route.repertoire_id,start,json.dumps([move]),day,(now-timedelta(days=10)).date().isoformat()))
            database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)",(route.repertoire_id,identifier))
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,
                decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json)
                VALUES(%s,1,%s,%s,%s,%s,%s,%s,'white','response',%s,%s,%s)""",
                (route.repertoire_id,route.route_id,index//2,identifier,opening_position_key(start),start,json.dumps([move]),index//2,index//2,json.dumps([opening_position_key(start)])))
        board.push_uci(move)
    for kind,identity in (("opening_graph_rebuild","r11-graph"),("integrity_scan","r11-scan")):
        database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
            VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""", (identity,kind,route.repertoire_id,
            json.dumps(dict(repertoire_id=route.repertoire_id,graph_generation=1)),stamp,stamp,stamp))
    database.execute_native("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status,
        scan_generation,scan_completed_sources,scan_total_sources) VALUES(%s,'clean',%s,'idle','r11-scan:1',1,1)""", (route.repertoire_id,stamp))
    database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,guided,source_kind,source_ref)
        VALUES('A','correct',%s,0,1,0,'study','unknown-historical-assistance')""", ((now-timedelta(days=3)).isoformat(),))


def main():
    if os.environ.get("TEMPO_TEST_INSTANCE")!="disposable":
        raise RuntimeError("R11 fixture requires the owning disposable test instance")
    action,identity,*args = sys.argv[1:]
    if not re.fullmatch(r"product-recombination-[a-f0-9]{32}",identity):
        raise ValueError("explicit fixture identity required")
    with psycopg.connect("postgresql://postgres@postgres:5432/tempo",row_factory=tempo_row_factory) as raw:
        if action=="readback":
            raw.execute("SET TRANSACTION READ ONLY")
        database = PostgresConnection(raw)
        now = datetime.now().astimezone()
        day = now.date().isoformat()
        protected = identity+"-protected"
        if action=="seed":
            assert not database.execute_native("SELECT 1 FROM cards WHERE id IN ('A','B','C')").fetchone(), "fixture identity already exists"
            seed_current_owner(database)
            # Legacy first-correct facts keep the already-introduced fixture in
            # recall mode. They supply no R11 event or known-assistance proof.
            database.execute_native("UPDATE cards SET first_correct_at=%s WHERE id IN ('A','B','C')",
                ((now-timedelta(days=10)).isoformat(),))
            database.execute_native("UPDATE repertoires SET source_name=%s,name='Connected practice fixture' WHERE id='r11-repertoire'",(identity,))
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Protected unrelated',%s,%s)",(protected,identity,now.isoformat()))
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,trained_color,
                canonical_route_source) VALUES(%s,%s,'response',%s,'["d2d4"]','learning',%s,%s,'white',0)""",
                (protected,protected,chess.STARTING_FEN,day,(now-timedelta(days=10)).date().isoformat()))
            database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)",(protected,protected))
            from app.services.review_service import ensure_card_queued_after
            ensure_card_queued_after(database,protected,0,queue_date=day,preserve_order=False)
            database.execute_native("""INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0)
                ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0""",(day,))
            print(json.dumps(dict(protected_id=protected,route_id="r11-route")))
            return
        assert database.execute_native("SELECT 1 FROM repertoires WHERE id='r11-repertoire' AND source_name=%s",(identity,)).fetchone(), "fixture not owned"
        if action=="practice":
            from tempo_legacy_database.adapters.study.recombination import CurrentStudyRecombination, PostgresRecombinationStore
            from app.services.review_service import ensure_card_queued_after, capture_daily_queue_order, preserve_daily_queue_order
            target = args[0]
            if target=="combined":
                from tempo_game_evidence_contracts.legacy import decision_identity
                target = PostgresRecombinationStore(database).current_presentation("r11-route",decision_identity("A",0,[]))
            boundary = CurrentStudyRecombination(database)
            current = boundary.try_current("r11-route")
            assert current is not None,boundary.reason
            assert any(m.presentation.presentation_id==target and m.available for m in current.members), "not current approved fixture material"
            # Deliberate extra same-day practice only; no review, assistance,
            # streak, FSRS value or success is seeded by this staging action.
            previous=capture_daily_queue_order(database,day)
            ensure_card_queued_after(database,target,0,attempt_state="clean",queue_date=day,preserve_order=False)
            preserve_daily_queue_order(database,day,previous=previous)
            database.execute_native("UPDATE queue_projections SET state='ready',refresh_pending=0 WHERE queue_date=%s",(day,))
            row = database.execute_native("SELECT start_fen,moves_json FROM cards WHERE id=%s",(target,)).fetchone()
            entry=database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND queue_date=%s AND status='queued' ORDER BY cycle DESC LIMIT 1",(target,day)).fetchone()[0]
            print(json.dumps(dict(card_id=target,queue_entry_id=entry,starting_fen=row[0],moves=json.loads(row[1]))))
            return
        if action!="readback":
            raise ValueError("seed, practice or readback required")
        generations = database.execute_native("""SELECT p.presentation_id,p.result_json,
            (SELECT state_json->>'count' FROM study_recombination_streaks s WHERE s.presentation_id=p.presentation_id
                AND s.evidence_epoch=p.result_json->>'evidence_epoch') clean_count,
            (SELECT count(*) FROM reviews r WHERE r.card_id=p.presentation_id AND r.source_kind='study') review_count
            FROM study_recombination_presentations p WHERE p.result_json->'presentation'->>'route_id'='r11-route'
            ORDER BY p.command_id,p.presentation_id""").fetchall()
        protected_row = database.execute_native("""SELECT q.card_id,q.status,o.issued_as_head,o.start_fen,o.moves_json,o.review_result_json
            FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=q.card_id
            WHERE q.card_id=%s ORDER BY q.cycle LIMIT 1""",(protected,)).fetchone()
        print(json.dumps(dict(generations=[dict(presentation_id=r[0],clean_count=int(r[2] or 0),review_count=r[3],
            components=len(r[1]["presentation"]["components"]),lineage=r[1]["presentation"]["lineage"]) for r in generations],
            changes=database.execute_native("SELECT count(*) FROM study_recombination_changes").fetchone()[0],
            original_cards=database.execute_native("SELECT count(*) FROM cards WHERE id IN ('A','B','C') AND archived=0 AND superseded_by IS NULL").fetchone()[0],
            unknown_history=database.execute_native("SELECT count(*) FROM reviews WHERE source_ref='unknown-historical-assistance'").fetchone()[0],
            study_counts=dict(tuple(row) for row in database.execute_native("""SELECT c.id,count(r.id) FROM cards c
                LEFT JOIN reviews r ON r.card_id=c.id AND r.source_kind='study'
                WHERE c.repertoire_id='r11-repertoire' GROUP BY c.id""").fetchall()),
            protected=list(protected_row) if protected_row else None)))


if __name__=="__main__":
    main()
