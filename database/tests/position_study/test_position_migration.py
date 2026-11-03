"""Migration 57 source registration and opt-in disposable PostgreSQL contract.

Native test requires TEMPO_POSITION_TEST_DSN pointing to a task-owned database
whose name starts with tempo_position_test_. It creates only a unique schema.
"""
from pathlib import Path
import os
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / 'database/tempo_legacy_database/ddl/migrations'
SQL = (MIGRATIONS / '057_position_study.sql').read_text()


def test_position_migration_registration_and_additive_scope():
    from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
    files = sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    assert [int(path.name[:3]) for path in files] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
    assert POSTGRES_SCHEMA_VERSION == 58
    assert SQL.count('INSERT INTO tempo_schema_migrations') == 1
    assert 'VALUES(57)' in SQL
    assert SQL.upper().count('CREATE TRIGGER') == 1
    assert 'BEFORE INSERT OR UPDATE OF id, fsrs_card_json ON cards' in SQL
    assert 'UPDATE cards' not in SQL
    assert 'ALTER TABLE ' not in SQL.upper()
    assert 'DROP TABLE ' not in SQL.upper()


def test_position_migration_native():
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn, autocommit=True) as conn:
        name = conn.execute('SELECT current_database()').fetchone()[0]
        assert name.startswith('tempo_position_test_'), 'refusing non-task-owned database'
        schema = 'position_f1_' + uuid4().hex
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute('CREATE TABLE tempo_schema_migrations (version integer PRIMARY KEY)')
            conn.execute('CREATE TABLE cards (id text PRIMARY KEY, fsrs_card_json text)')
            conn.execute("INSERT INTO cards VALUES (%s,%s)", ('retained', '{"card_id":4503599627370496,"memory":1}'))
            conn.execute("INSERT INTO cards VALUES (%s,%s)", ('z-reserved', '{"card_id":4503599627370497}'))
            conn.execute("INSERT INTO cards VALUES ('malformed', 'old bad json')")
            conn.execute("INSERT INTO cards VALUES (%s,%s)", ('string', '{"card_id":"123","memory":1}'))
            with conn.transaction():
                conn.execute(SQL)
            assert conn.execute('SELECT version FROM tempo_schema_migrations').fetchall() == [(57,)]
            assert conn.execute("SELECT fsrs_card_json FROM cards WHERE id='retained'").fetchone()[0] == '{"card_id":4503599627370496,"memory":1}'
            conn.execute("UPDATE cards SET fsrs_card_json=%s WHERE id='retained'", ('{"card_id":4503599627370496,"memory":2}',))
            # A preflight page ending at m cannot miss a reserved z-row by rename.
            assert conn.execute("SELECT id FROM cards WHERE id > 'm' ORDER BY id").fetchall()[-1] == ('z-reserved',)
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE cards SET id='a-reserved' WHERE id='z-reserved'")
            assert conn.execute("SELECT id FROM cards WHERE id > 'm' ORDER BY id").fetchall()[-1] == ('z-reserved',)
            conn.execute("UPDATE cards SET id='a-malformed' WHERE id='malformed'")
            conn.execute("UPDATE cards SET fsrs_card_json='another bad json' WHERE id='a-malformed'")
            conn.execute("UPDATE cards SET fsrs_card_json=%s WHERE id='string'", ('{"card_id":"123","memory":2}',))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE cards SET fsrs_card_json=%s WHERE id='string'", ('{"card_id":"124"}',))
            conn.execute("INSERT INTO cards VALUES (%s,%s)", ('notation', '{"card_id":1.73e12}'))
            conn.execute("INSERT INTO cards VALUES (%s,%s)", ('ordinary', '{"card_id":1730000000000}'))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO cards VALUES (%s,%s)", ('collision', '{"card_id":4503599627370496}'))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE cards SET fsrs_card_json=%s WHERE id='ordinary'", ('{"card_id":4503599627370497}',))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO cards VALUES (%s,%s)", ('unicode', '{"card_id":"４５０３５９９６２７３７０４９６"}'))
            guard = conn.execute("""SELECT pg_get_triggerdef(oid) FROM pg_trigger
                WHERE tgrelid='cards'::regclass AND tgname='study_position_guard_legacy_fsrs_id'""").fetchone()[0]
            assert 'BEFORE INSERT OR UPDATE OF id, fsrs_card_json' in guard
            allocated = conn.execute("SELECT nextval('study_position_fsrs_id_seq')").fetchone()[0]
            assert allocated == 4503599627370496
            conn.execute("INSERT INTO study_position_epochs VALUES ('r','e','source',now()), ('other','e','source',now())")
            conn.execute("""INSERT INTO study_position_seed_runs
                (repertoire_id,owner_epoch,source_digest,source_token_json,selection_policy,manifest_digest,
                 manifest_count,allocator_preflight_json,created_at)
                VALUES ('r','e','source','{}','policy','manifest',1,'{}',now())""")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("""UPDATE study_position_seed_runs SET status='complete',completed_at=now()
                    WHERE repertoire_id='r' AND owner_epoch='e'""")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("""INSERT INTO study_position_seed_outcomes
                    (repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,
                     donor_card_id,donor_card_revision,donor_fingerprint,donor_selection_policy,
                     donor_fsrs_internal_card_id,allocated_fsrs_internal_card_id,clone_fingerprint,introduced_at)
                    VALUES ('r','e','standard','fen','e2e4','v','seeded','card',1,'hash','policy',
                            %s,%s,'clone',now())""", (allocated, allocated))
            conn.execute("""INSERT INTO study_position_seed_outcomes
                (repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,hold_reason)
                VALUES ('r','e','standard','fen','e2e4','v','hold','pending')""")
            conn.execute("""UPDATE study_position_seed_runs SET held_count=1,status='complete',completed_at=now()
                WHERE repertoire_id='r' AND owner_epoch='e'""")
            conn.execute("INSERT INTO study_position_owners(repertoire_id,epoch,updated_at) VALUES ('r','e',now())")
            assert conn.execute("SELECT mode FROM study_position_owners").fetchone() == ('inactive',)
            conn.execute("""INSERT INTO study_position_states
                (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                 fsrs_internal_card_id,fsrs_json,due_at)
                VALUES ('r','standard','fen','e2e4','v','e','source',7,'{}',now())""")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("""INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     fsrs_internal_card_id,fsrs_json,due_at)
                    VALUES ('other','standard','fen','e2e4','v','e','source',7,'{}',now())""")
            conn.execute("""INSERT INTO study_position_issued_plans
                VALUES ('a',1,'card',1,'r','standard','e','source','2026-11-02','digest','{}',now())""")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("""INSERT INTO study_position_issued_plans
                    VALUES ('duplicate',1,'card',1,'r','standard','e','source','2026-11-02','other','{}',now())""")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute("""INSERT INTO study_position_first_responses
                    VALUES ('event','a',0,'other','standard','fen','e','digest','{}',now(),now())""")
            conn.execute("""INSERT INTO study_position_first_responses
                VALUES ('event','a',0,'r','standard','fen','e','digest','{}',now(),now())""")
            conn.execute("""INSERT INTO study_position_grades
                (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,variant,
                 position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,
                 request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES ('g','study','event','a',0,'response','r','standard','fen','e','e2e4','v','source',
                        '2026-11-02','good','digest',now(),'{}','{}',1,2)""")
            conn.execute("INSERT INTO study_position_epochs VALUES ('r','e2','new source',now())")
            conn.execute("""INSERT INTO study_position_issued_plans
                VALUES ('a2',2,'card',2,'r','standard','e2','new source','2026-11-02','d2','{}',now())""")
            conn.execute("""INSERT INTO study_position_first_responses
                VALUES ('event2','a2',0,'r','standard','fen','e2','digest2','{}',now(),now())""")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("""INSERT INTO study_position_grades
                    (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,variant,
                     position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,
                     request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                    VALUES ('g2','study','event2','a2',0,'response','r','standard','fen','e2','e2e4','v2',
                            'new source','2026-11-02','good','digest2',now(),'{}','{}',2,3)""")
            conn.execute("""INSERT INTO study_position_grades
                (effect_id,source_kind,game_effect_key,game_evidence_json,effect_kind,repertoire_id,variant,
                 position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,
                 request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES ('game','game_lapse','game-key','{}','lapse','r','standard','fen','e2','e2e4','v2',
                        'new source','2026-11-02','again','game-digest',now(),'{}','{}',2,3)""")
            assert conn.execute("SELECT attempt_id FROM study_position_grades WHERE effect_id='game'").fetchone() == (None,)

        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_readiness_queries_all_position_tables():
    from contextlib import contextmanager
    from datetime import date
    from tempo_legacy_database.adapters.postgres_readiness import read_health_facts

    statements = []
    class Cursor:
        def fetchone(self):
            return (True,)
    class Reader:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            return Cursor()
    @contextmanager
    def reader():
        yield Reader()
    facts = read_health_facts(reader, lambda: date(2026, 11, 2))
    assert facts[1] == (True,)
    for table in ('epochs', 'owners', 'states', 'fsrs_id_seq', 'seed_runs', 'seed_outcomes', 'issued_plans', 'first_responses', 'grades'):
        assert "to_regclass('public.study_position_" + table + "')" in statements[1]
