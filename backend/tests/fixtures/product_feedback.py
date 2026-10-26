"""Synthetic fixture/readback for the owning disposable PostgreSQL browser run."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys

import chess
from fsrs import Card
import psycopg
from tempo_game_evidence_contracts.legacy import decision_identity


def main():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("product feedback fixture requires the owning disposable test instance")
    action, identity = sys.argv[1:]
    if not re.fullmatch(r"product-feedback-[a-f0-9]{32}", identity):
        raise ValueError("one explicit task fixture identity required")
    target, protected, game = identity + "-target", identity + "-protected", "lichess:" + identity
    repertoire, protected_rep = identity + "-repertoire", identity + "-protected-repertoire"
    board = chess.Board()
    board.remove_piece_at(chess.D8)
    target_fen = board.fen(en_passant="legal")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with psycopg.connect("postgresql://postgres@postgres:5432/tempo") as database:
        if action == "readback":
            database.execute("SET TRANSACTION READ ONLY")
            counts = database.execute("""SELECT
                (SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'),
                (SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='study'),
                (SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s),
                (SELECT status FROM study_game_feedback WHERE card_id=%s)""", (target,target,target,target)).fetchone()
            print(json.dumps(dict(lapses=counts[0],study_reviews=counts[1],review_receipts=counts[2],status=counts[3])))
            return
        if action != "seed":
            raise ValueError("seed or readback required")
        for rep, card_id, fen, move, title in (
            (repertoire,target,target_fen,"e2e4","Feedback target"),
            (protected_rep,protected,chess.STARTING_FEN,"d2d4","Protected unrelated"),
        ):
            route, scan = rep + "-route", rep + "-scan"
            position = " ".join(fen.split()[:4])
            state = Card(card_id=100,due=now+timedelta(days=20),last_review=now-timedelta(days=3),stability=20,difficulty=5).to_json()
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,%s,'synthetic',%s,0)", (rep,title,now.isoformat()))
            database.execute("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(%s,%s,'Fixture route','white',%s,%s,%s)""", (route,rep,fen,json.dumps([move]),now.isoformat()))
            database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,
                revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type,trained_color)
                VALUES(%s,%s,'prefix',%s,%s,'mature',%s,%s,1,20,%s,%s,20,'opening','white')""",
                (card_id,rep,fen,json.dumps([move]),(now+timedelta(days=20)).date().isoformat(),
                 (now-timedelta(days=6)).isoformat(),state,(now-timedelta(days=5)).isoformat()))
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)", (rep,card_id))
            database.execute("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
                internal_rating,guided,source_kind,source_ref) VALUES(%s,'correct',%s,10,20,'good',0,'study',%s)""",
                (card_id,(now-timedelta(days=3)).isoformat(),"retained:"+card_id))
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (rep,now.isoformat()))
            database.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,
                decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json)
                VALUES(%s,1,%s,0,%s,%s,%s,%s,'white','prefix',0,0,%s)""", (rep,route,card_id,position,fen,json.dumps([move]),json.dumps([position])))
            for kind, task_id in (("opening_graph_rebuild",rep+"-graph"),("integrity_scan",scan)):
                database.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,
                    next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""",
                    (task_id,kind,rep,json.dumps(dict(repertoire_id=rep,graph_generation=1)),now.isoformat(),now.isoformat(),now.isoformat()))
            database.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status,
                scan_generation,scan_completed_sources,scan_total_sources) VALUES(%s,'clean',%s,'idle',%s,1,1)""", (rep,now.isoformat(),scan+":1"))
        database.execute("""INSERT INTO imported_games(id,provider,provider_game_id,username,played_at,speed,rated,
            color,result,start_fen,moves_json,content_hash) VALUES(%s,'lichess',%s,'synthetic',%s,'rapid',0,'white','draw',%s,'["d2d4"]',%s)""",
            (game,identity,(now-timedelta(days=1)).isoformat(),target_fen,"a"*64))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(%s,%s,0,'review')", (now.date().isoformat(),protected))
        database.execute("""INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
            VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0""", (now.date().isoformat(),))
    print(json.dumps(dict(game_id=game,repertoire_id=repertoire,target_id=target,protected_id=protected,
        decision_id=decision_identity(target,0,[]),target_fen=target_fen,expected="e2e4",played="d2d4")))


if __name__ == "__main__":
    main()
