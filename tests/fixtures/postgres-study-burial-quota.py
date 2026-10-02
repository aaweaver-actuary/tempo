"""Real PostgreSQL quota proof, run only inside the disposable durability stack."""

from datetime import date
import json

from app import postgres_store, queue_commands  # Registers the production bury handler.
from app.command_gateway import execute_command
from app.services import postgres_queue_refresh
from app.services.review_service import preserve_daily_queue_order

assert postgres_store.configured(), "This regression requires real PostgreSQL"
queue_date = date.today().isoformat()
start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
with postgres_store.connection() as database:
    database.execute("UPDATE settings SET study_new_per_day=1 WHERE id=1")
    database.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES('pg-bury-study','Burial quota',?,?)", (queue_date, queue_date))
    database.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES('pg-bury-chapter','pg-bury-study','Chapter',0)")
    database.execute("""INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at)
        VALUES('pg-bury-source','pg-bury-chapter','pg-bury-group',1,'','hash','burial.pgn',0,'{}','[]',1,?)""", (queue_date,))
    database.execute("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path) VALUES('pg-bury-position','pg-bury-source',0,?,'[]','0')", (start_fen,))
    for exercise_id in ("pg-bury-quota-a", "pg-bury-quota-b"):
        database.execute("INSERT INTO study_exercises(id,study_id,position_id,status,created_at,updated_at) VALUES(?,'pg-bury-study','pg-bury-position','published',?,?)", (exercise_id, queue_date, queue_date))
        database.execute("""INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at)
            VALUES(?,1,'{"type":"choice","prompt":"Choose","hint":"","explanation":"","options":[{"id":"a","text":"A"}],"correct_option_ids":["a"]}','hash',?)""", (exercise_id, queue_date))
        database.execute("""INSERT INTO cards(id,kind,start_fen,moves_json,state,due_date,content_type,study_exercise_id)
            VALUES(?,'exercise',?,'[]','new',?,'study_exercise',?)""", (exercise_id, start_fen, queue_date, exercise_id))

assert postgres_queue_refresh._prepare_study_admission(queue_date) == "pg-bury-quota-a"
with postgres_store.connection() as database:
    assert postgres_queue_refresh._admit_one_study_card(database, queue_date, "pg-bury-quota-a")
    selected_entry = database.execute("SELECT id FROM daily_queue WHERE card_id='pg-bury-quota-a' AND queue_date=?", (queue_date,)).fetchone()[0]
    # Fixture positioning puts the selected Study admission at the authoritative
    # head while retaining unrelated cards for the order assertion.
    database.execute("UPDATE daily_queue SET position=-1 WHERE id=?", (selected_entry,))
    preserve_daily_queue_order(database, queue_date)
    original_card = dict(database.execute("SELECT * FROM cards WHERE id='pg-bury-quota-a'").fetchone())
    unrelated_order = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id", (queue_date, selected_entry))]

operation_id = "pg-study-quota-bury"
result = execute_command(operation_id, "queue.bury", {"entry_id": selected_entry})
assert result == {"buried": True, "queue_entry_id": selected_entry}
# These execute production admission SQL on PostgreSQL. The second assertion
# models a candidate prepared before burial that reaches the locked recheck.
assert postgres_queue_refresh._prepare_study_admission(queue_date) is None, "Burial must still consume the Study quota"
with postgres_store.connection() as database:
    assert not postgres_queue_refresh._admit_one_study_card(database, queue_date, "pg-bury-quota-b"), "Locked admission must reject a replacement after burial"
    assert dict(database.execute("SELECT * FROM cards WHERE id='pg-bury-quota-a'").fetchone()) == original_card
    assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id IN ('pg-bury-quota-a','pg-bury-quota-b')").fetchone()[0] == 0
    assert [tuple(row) for row in database.execute("SELECT card_id,status FROM daily_queue WHERE queue_date=? AND card_id IN ('pg-bury-quota-a','pg-bury-quota-b')", (queue_date,))] == [("pg-bury-quota-a", "buried")]
assert execute_command(operation_id, "queue.bury", {"entry_id": selected_entry}) == result
print(json.dumps({"unrelated_order": unrelated_order, "queue_date": queue_date}))
