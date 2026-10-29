"""Native upgrade fixtures for exact retired-family deletion; disposable runner only."""
from pathlib import Path
import json
import runpy
import psycopg
from psycopg import sql
import pytest

ROOT = Path(__file__).resolve().parents[2]
FEN = "8/8/8/8/8/2k5/R7/1K6 w - - 0 1"


def old_family_tables(owner):
    source = (ROOT / ("database/tempo_learning_database/ddl/migrations/0001_core.sql" if owner == "learning" else "database/tempo_legacy_database/ddl/migrations/001_initial.sql")).read_text()
    return [statement.strip().split("CREATE TABLE ", 1)[-1] for statement in source.split(";")
            if any("CREATE TABLE " + name + " (" in statement for name in ("endgame_templates", "endgame_attempts", "tablebase_cache"))]


def seed(database):
    for identifier in ("__endgames__", "retirement-kept"):
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'retirement fixture','2026-10-29')", (identifier, identifier))
    for identifier, owner, family in [("retired", "__endgames__", "endgame"), ("kept", "retirement-kept", "tactic")]:
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(%s,%s,'checkpoint',%s,'[]','learning','2026-10-29',%s,'white')", (identifier, owner, FEN, family))
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)", (owner, identifier))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-29',%s,%s)", (identifier, 0 if identifier == 'kept' else 1))
        review = database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct','2026-10-29',1,2) RETURNING id", (identifier,)).fetchone()[0]
        database.execute("INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES(%s,'{}')", (review,))
    database.execute("INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES('template','retired','Old material','KR','K','white','both','2026-10-29')")
    database.execute("INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES('attempt','template',%s,'win','2026-10-29')", (FEN,))
    for identifier, command in [('retired-operation', 'endgames.attempt.create'), ('kept-operation', 'cards.review')]:
        database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,payload_json,state) VALUES(%s,%s,'unchanged-hash','{}','queued')", (identifier, command))


def kept_state(database):
    return {table: database.execute(statement).fetchall() for table, statement in {
        'cards': "SELECT * FROM cards WHERE id='kept'",
        'reviews': "SELECT * FROM reviews WHERE card_id='kept'",
        'queue': "SELECT * FROM daily_queue WHERE card_id='kept'",
        'memberships': "SELECT * FROM repertoire_cards WHERE card_id='kept'",
        'operation': "SELECT * FROM operation_receipts WHERE operation_id='kept-operation'",
        'snapshots': "SELECT * FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id='kept')",
    }.items()}


def assert_retirement(admin_url, owner, conflict=False):
    namespace = 'learning' if owner == 'learning' else 'public'
    if owner == 'legacy':
        legacy = runpy.run_path(str(ROOT / 'database/tooling/apply_postgres_migrations.py'))
        with psycopg.connect(admin_url) as database:
            for path in sorted(legacy['MIGRATIONS'].glob('[0-9][0-9][0-9]_*.sql')):
                if int(path.name[:3]) < 55:
                    database.execute(legacy['migration_source'](path), prepare=False)
        migrate = lambda: legacy['apply_migrations'](admin_url)
    else:
        from tempo_learning_database.ddl.migrate import migrate as learning_migrate
        with psycopg.connect(admin_url) as database:
            database.execute('SET LOCAL search_path=learning,pg_catalog')
            database.execute('ALTER TABLE cards DROP CONSTRAINT retired_practice_family')
            for definition in old_family_tables(owner):
                database.execute('CREATE TABLE ' + definition)
            database.execute("DELETE FROM migration_history WHERE id='learning.runtime/0004'")
        migrate = lambda: learning_migrate(admin_url, 'workspace-a', 'America/New_York')
    with psycopg.connect(admin_url) as database:
        database.execute(sql.SQL('SET LOCAL search_path={},pg_catalog').format(sql.Identifier(namespace)))
        seed(database)
        if conflict:
            database.execute("UPDATE endgame_templates SET card_id='kept'")
        before = kept_state(database)
    if conflict:
        with pytest.raises(psycopg.errors.RaiseException, match='cross-family ownership'):
            migrate()
        with psycopg.connect(admin_url) as database:
            database.execute(sql.SQL('SET LOCAL search_path={},pg_catalog').format(sql.Identifier(namespace)))
            assert kept_state(database) == before
            assert database.execute("SELECT count(*) FROM cards WHERE id='retired'").fetchone()[0] == 1
            assert database.execute("SELECT state FROM operation_receipts WHERE operation_id='retired-operation'").fetchone()[0] == 'queued'
        return
    migrate()
    migrate()  # Owner history makes upgrade replay a no-op.
    with psycopg.connect(admin_url) as database:
        database.execute(sql.SQL('SET LOCAL search_path={},pg_catalog').format(sql.Identifier(namespace)))
        assert kept_state(database) == before
        assert database.execute("SELECT count(*) FROM cards WHERE content_type='endgame'").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM reviews WHERE card_id='retired'").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM queue_attempt_origins WHERE card_id='retired'").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM deleted_cards WHERE card_id='retired'").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM repertoires WHERE id='__endgames__'").fetchone()[0] == 0
        state, error = database.execute("SELECT state,error_json FROM operation_receipts WHERE operation_id='retired-operation'").fetchone()
        assert state == 'failed' and json.loads(error)['status_code'] == 410
        for table in ('endgame_templates', 'endgame_attempts', 'tablebase_cache'):
            assert database.execute("SELECT to_regclass(%s)", (namespace + '.' + table,)).fetchone()[0] is None
        with pytest.raises(psycopg.errors.CheckViolation), database.transaction():
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES('new-ending','retirement-kept','checkpoint',%s,'[]','2026-10-29','endgame')", (FEN,))


def seed_sqlite_retirement(database, conflict=False):
    """Synthetic pre-retirement records in the backend's temporary SQLite fixture."""
    for name in ('retired_practice_insert', 'retired_practice_update'):
        database.execute(f'DROP TRIGGER IF EXISTS {name}')
    for definition in old_family_tables('legacy'):
        database.execute('CREATE TABLE ' + definition)
    database.execute('ALTER TABLE settings ADD COLUMN draw_hold_user_moves INTEGER DEFAULT 20')
    for identifier in ('__endgames__', 'retirement-kept'):
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,'retirement fixture','2026-10-29')", (identifier, identifier))
    for identifier, owner, family in [('retired','__endgames__','endgame'),('kept','retirement-kept','tactic')]:
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,?,'checkpoint',?,'[]','learning','2026-10-29',?,'white')", (identifier, owner, FEN, family))
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (owner, identifier))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-29',?,?)", (identifier, 0 if identifier=='kept' else 1))
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct','2026-10-29',1,2)", (identifier,))
    database.execute("INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES('template',?,'Old material','KR','K','white','both','2026-10-29')", ('kept' if conflict else 'retired',))
    database.execute("INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES('attempt','template',?,'win','2026-10-29')", (FEN,))
    return {table: [tuple(row) for row in database.execute(query)] for table,query in {
        'cards': "SELECT * FROM cards WHERE id='kept'", 'reviews': "SELECT * FROM reviews WHERE card_id='kept'",
        'queue': "SELECT * FROM daily_queue WHERE card_id='kept'"}.items()}


def assert_sqlite_kept_state(database, before):
    for table, query in {'cards': "SELECT * FROM cards WHERE id='kept'", 'reviews': "SELECT * FROM reviews WHERE card_id='kept'", 'queue': "SELECT * FROM daily_queue WHERE card_id='kept'"}.items():
        assert [tuple(row) for row in database.execute(query)] == before[table]
