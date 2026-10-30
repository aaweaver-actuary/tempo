"""Real review/queue transactions in an owned freshly migrated disposable database."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))
from contextlib import ExitStack
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app import main, queue_commands, review_commands
from app.services.queue_ordering import PRESENTATION_VERSION
from app.services.real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters.http_composition.queue_projection import queue_rows
from tempo_legacy_database.adapters import postgres_store
from app.contracts.transport.study import ReviewRequest
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.durable_tasks import claim_task
from app.services.postgres_queue_refresh import execute_postgres_queue_refresh_slice


def seed(database, identifier, today):
    root_id, child_id, grandchild_id = [identifier + suffix for suffix in ('-root', '-child', '-grandchild')]
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,'Exposure progression','synthetic',?,2)", (identifier, today))
    database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Main line','white',?,'[\"e2e4\",\"e7e5\",\"g1f3\",\"b8c6\",\"f1b5\"]',?)", (identifier, identifier, chess.STARTING_FEN, today))
    database.execute('INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)', (identifier, today))
    board = chess.Board()
    for index, (card_id, moves) in enumerate(zip((root_id, child_id, grandchild_id), (['e2e4'], ['e7e5','g1f3'], ['b8c6','f1b5']))):
        starting_fen = board.fen()
        for move in moves:
            decision_fen = ' '.join(board.fen().split()[:4])
            board.push_uci(move)
        parent_id = None if index == 0 else (root_id, child_id)[index - 1]
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,trained_color,canonical_route_source) VALUES(?,?,?,?,?,?,?,?,'white',0)",
                         (card_id, identifier, 'prefix' if index == 0 else 'response', starting_fen, json.dumps(moves), 'learning' if index == 0 else 'locked', today, today if index == 0 else None))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (identifier, card_id))
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,decision_fen_keys_json) VALUES(?,1,?,?,?,?,?,?,?,'white',?,?)",
                         (identifier, identifier, index, card_id, parent_id, decision_fen, starting_fen, json.dumps(moves), 'prefix' if index == 0 else 'decision', json.dumps([decision_fen])))
    root_entry = database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,0,'new',?) RETURNING id", (today, root_id, identifier)).fetchone()[0]
    return root_id, child_id, grandchild_id, root_entry


def seed_ready_v3_day(database, identifier, today, root_entry):
    """Real old ready projection, >20 reviews and retained attempt/retry slots."""
    import hashlib
    repertoire = identifier + '-ordinary'
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,'Ordinary review sentinels','synthetic',?,0)", (repertoire, today))
    for index in range(24):
        card_id = repertoire + f'-{index:02d}'
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,content_type,trained_color,canonical_route_source) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,'2000-01-01','opening','white',0)",
                         (card_id, repertoire, chess.STARTING_FEN, today))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (repertoire, card_id))
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'correct','2000-01-01T00:00:00+00:00',0,1,'study')", (card_id,))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle,attempt_state,admission_kind,admission_repertoire_id) VALUES(?,?,?,?,?,'review',?)",
                         (today, card_id, index, 1 if index == 9 else 0, 'guided' if index == 9 else 'clean', repertoire))
    database.execute('UPDATE daily_queue SET position=24 WHERE id=?', (root_entry,))
    database.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id IN (SELECT id FROM daily_queue WHERE queue_date=? AND position=3)", (today,))
    members = [tuple(row) for row in database.execute("SELECT id,card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY id", (today,))]
    fingerprint = 'queue-presentation-v3:' + hashlib.sha256(json.dumps(members, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    database.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(?,42,?,?) ON CONFLICT(queue_date) DO UPDATE SET seed=42,membership_hash=excluded.membership_hash", (today, fingerprint, today))
    database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(?,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (today,))
    return repertoire


def protected_positions(database, today):
    return [tuple(row) for row in database.execute(
        "SELECT q.id,q.card_id,q.cycle,q.position FROM daily_queue q WHERE q.queue_date=? AND q.status='queued' AND (q.cycle>0 OR EXISTS(SELECT 1 FROM queue_attempt_origins o WHERE o.queue_entry_id=q.id AND o.issued_as_head=1)) ORDER BY q.id", (today,))]


def snapshot(database, table, *, order='id'):
    return [dict(row) for row in database.execute(f'SELECT * FROM {table} ORDER BY {order}')]


def drain_queue_refresh(*, restart=False, preserve_through_expected=True):
    for slice_index in range(200):
        task = claim_task('daily_queue')
        assert task is not None, 'Expected a runnable bounded queue slice'
        if restart and slice_index == 0:
            with postgres_store.connection(read_only=True) as database:
                before_denial = dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone())
            entered = Event()
            completed = Event()
            failures = []
            def run():
                entered.set()
                try:
                    execute_postgres_queue_refresh_slice(task)
                except BaseException as error:
                    failures.append(error)
                finally:
                    completed.set()
            with activity_gate.foreground():
                worker = Thread(target=run)
                worker.start()
                assert entered.wait(2)
                assert completed.wait(2), 'Denied background slice occupied the worker'
                assert len(failures) == 1 and isinstance(failures[0], BackgroundAdmissionDeferred), failures
                with postgres_store.connection(read_only=True) as database:
                    assert database.execute('SELECT 1').fetchone()[0] == 1
                    assert dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone()) == before_denial
            worker.join(5)
            assert not worker.is_alive()
            execute_postgres_queue_refresh_slice(task)
        else:
            execute_postgres_queue_refresh_slice(task)
        if restart and slice_index == 1:
            crashed_task = claim_task('daily_queue')
            assert crashed_task is not None
            with postgres_store.connection() as database:
                database.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (crashed_task['id'],))
            postgres_store.close_pools()
            resumed_task = claim_task('daily_queue')
            assert resumed_task['lease_token'] != crashed_task['lease_token']
            assert execute_postgres_queue_refresh_slice(crashed_task) is False
            execute_postgres_queue_refresh_slice(resumed_task)
        with postgres_store.connection(read_only=True) as database:
            saved = database.execute("SELECT state,payload_json FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
            if preserve_through_expected:
                assert 'preserve_through_entry_id' in json.loads(saved['payload_json'])
            if saved['state'] == 'complete':
                return
    raise AssertionError('Queue refresh failed to reach a durable complete projection')


def test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound(database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Opening progression proof requires an explicitly disposable stack')
    original_write_url = os.environ.get('TEMPO_DATABASE_WRITE_URL')
    original_read_url = os.environ.get('TEMPO_DATABASE_READ_URL')
    fixture_database = 'opening_progression_' + uuid.uuid4().hex
    connection_info = psycopg.conninfo.conninfo_to_dict(database_url)
    postgres_store.close_pools()
    admin_info = {**connection_info, 'dbname': 'postgres'}
    fixture_scope = ExitStack()
    try:
        with psycopg.connect(**admin_info, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(fixture_database)))
        fixture_url = psycopg.conninfo.make_conninfo(**{**connection_info, 'dbname': fixture_database})
        from database.tooling.apply_postgres_migrations import apply_migrations
        apply_migrations(fixture_url)
        os.environ['TEMPO_DATABASE_WRITE_URL'] = fixture_url
        os.environ['TEMPO_DATABASE_READ_URL'] = fixture_url
        from database.tests.check_postgres_graph_retention import owned_admission_scope
        fixture_scope.enter_context(owned_admission_scope(fixture_database))
        today = date.today().isoformat()
        with postgres_store.connection() as database:
            database.execute('INSERT INTO settings(id,tactics_new_per_day,study_new_per_day) VALUES(1,0,0)')
        for outcome, guided in [('correct', False), ('again', False), ('correct', True)]:
            identifier = 'practice-' + uuid.uuid4().hex
            with postgres_store.connection() as database:
                root_id, child_id, grandchild_id, root_entry = seed(database, identifier, today)
                main._unlock_eligible_opening_cards(database, today, after_card_id='', batch_size=8)
                assert database.execute('SELECT state FROM cards WHERE id=?', (child_id,)).fetchone()[0] == 'locked'
                ordinary_repertoire = seed_ready_v3_day(database, identifier, today, root_entry)
                identities = [tuple(row) for row in database.execute('SELECT id,card_id,cycle FROM daily_queue ORDER BY id')]
                before_cards = snapshot(database, 'cards')
                before_reviews = snapshot(database, 'reviews')
                before_origins = snapshot(database, 'queue_attempt_origins', order='queue_entry_id')
                before_protected = protected_positions(database, today)
                assert database.execute('SELECT position FROM daily_queue WHERE id=?', (root_entry,)).fetchone()[0] == 24
                requested = queue_commands.ensure_current_queue(database, {'queue_date': today})
                assert requested['refresh_pending'] is True, 'A nonempty ready v3 day must request normal refresh'
            drain_queue_refresh(preserve_through_expected=False)
            with postgres_store.connection() as database:
                assert [tuple(row) for row in database.execute('SELECT id,card_id,cycle FROM daily_queue ORDER BY id')] == identities
                assert snapshot(database, 'cards') == before_cards
                assert snapshot(database, 'reviews') == before_reviews
                assert snapshot(database, 'queue_attempt_origins', order='queue_entry_id') == before_origins
                assert protected_positions(database, today) == before_protected
                complete = queue_rows(database, today, None, miss_reason=MISS_REASON)
                window = queue_rows(database, today, 20, miss_reason=MISS_REASON)
                assert len(complete) == 25 and len(window) == 20
                assert [row['queue_entry_id'] for row in window] == [row['queue_entry_id'] for row in complete[:20]]
                assert complete[0]['queue_entry_id'] == root_entry
                assert database.execute('SELECT membership_hash FROM daily_queue_days WHERE queue_date=?', (today,)).fetchone()[0].startswith(PRESENTATION_VERSION + ':')
                completed_task = snapshot(database, 'background_tasks')
                assert queue_commands.ensure_current_queue(database, {'queue_date': today}) == {'queue_date': today, 'refresh_pending': False}
                assert snapshot(database, 'background_tasks') == completed_task
            request = ReviewRequest(queue_entry_id=root_entry, outcome=outcome, guided=guided,
                                    attempt_id=identifier+'-attempt', expected_revision=1)
            payload = {'card_id': root_id, 'review': request.model_dump(mode='json')}
            try:
                with postgres_store.connection() as database:
                    review_commands.submit_review(database, payload)
                    raise ValueError('Simulated crash before review commit')
            except ValueError as error:
                assert str(error) == 'Simulated crash before review commit'
            with postgres_store.connection(read_only=True) as database:
                assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (root_id,)).fetchone()[0] == 0
                assert database.execute('SELECT COUNT(*) FROM review_attempt_receipts WHERE attempt_id=?', (request.attempt_id,)).fetchone()[0] == 0
            with postgres_store.connection() as database:
                result = review_commands.submit_review(database, payload)
                assert result['state'] == 'learning'
                generation = database.execute("SELECT generation FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()[0]
                preserved_ids = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (today,))]
                before_child_protected = protected_positions(database, today)
                protected_ids = {row[0] for row in before_child_protected}
                ordinary_ids = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND admission_repertoire_id=? AND status='queued' ORDER BY position,id", (today, ordinary_repertoire)) if row[0] not in protected_ids]
            postgres_store.close_pools()
            with postgres_store.connection() as database:
                assert review_commands.submit_review(database, payload) == result
                assert database.execute("SELECT generation FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()[0] == generation
            drain_queue_refresh(restart=True)
            with postgres_store.connection() as database:
                assert database.execute('SELECT state FROM cards WHERE id=?', (child_id,)).fetchone()[0] == 'learning'
                assert database.execute('SELECT state FROM cards WHERE id=?', (grandchild_id,)).fetchone()[0] == 'locked'
                assert database.execute('SELECT state FROM cards WHERE id=?', (root_id,)).fetchone()[0] == 'learning'
                queued_ids = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (today,))]
                assert protected_positions(database, today) == before_child_protected
                child_entry = database.execute('SELECT id FROM daily_queue WHERE card_id=? AND cycle=0', (child_id,)).fetchone()[0]
                assert set(queued_ids) == set(preserved_ids) | {child_entry}
                assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND cycle=0', (child_id,)).fetchone()[0] == 1
                assert [entry for entry in queued_ids if entry in ordinary_ids] == ordinary_ids
                assert all(queued_ids.index(child_entry) < queued_ids.index(entry) for entry in ordinary_ids)
                assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (root_id,)).fetchone()[0] == 1
                child_request = ReviewRequest(queue_entry_id=child_entry, outcome='again', attempt_id=identifier+'-child-attempt', expected_revision=1)
                review_commands.submit_review(database, {'card_id': child_id, 'review': child_request.model_dump(mode='json')})
            drain_queue_refresh()
            with postgres_store.connection() as database:
                assert database.execute('SELECT state FROM cards WHERE id=?', (grandchild_id,)).fetchone()[0] == 'new'
                assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?', (grandchild_id,)).fetchone()[0] == 0
                assert database.execute('SELECT COUNT(*) FROM cards WHERE repertoire_id=? AND introduced_at=?', (identifier, today)).fetchone()[0] == 2
                database.execute('DELETE FROM cards WHERE repertoire_id=?', (ordinary_repertoire,))
                database.execute('DELETE FROM repertoires WHERE id=?', (ordinary_repertoire,))
                database.execute('DELETE FROM cards WHERE repertoire_id=?', (identifier,))
                database.execute('DELETE FROM repertoires WHERE id=?', (identifier,))
        print('PASS test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound')
    finally:
        fixture_scope.close()
        postgres_store.close_pools()
        for key, value in [('TEMPO_DATABASE_WRITE_URL', original_write_url), ('TEMPO_DATABASE_READ_URL', original_read_url)]:
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with psycopg.connect(**admin_info, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(fixture_database)))


def test_postgres_queue_publication_with_historical_and_null_evidence(database_url):
    """Full publication stays within 250 ms without weakening its state fences.

    The current-day queue estimate is deliberately stale. Historical evidence
    includes a legacy NULL queue-entry identity, which protects no current row.
    This runs in its own fresh database so progression's history stays pristine.
    """
    import hashlib
    import time
    from app.services import queue_ordering
    from app.services import postgres_queue_refresh as refresh
    from tempo_legacy_database.adapters.queue_refresh import presentation as storage
    from database.tooling.apply_postgres_migrations import apply_migrations
    from database.tests.check_postgres_graph_retention import owned_admission_scope

    assert os.environ.get('TEMPO_TEST_INSTANCE') == 'disposable'
    assert 0 < int(os.environ.get('TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS', '250')) <= 250
    admin_dsn = database_url
    info = psycopg.conninfo.conninfo_to_dict(admin_dsn)
    name = 'presentation_protection_' + uuid.uuid4().hex
    marker = 'tempo:new-first-publication:' + name
    fixture_dsn = psycopg.conninfo.make_conninfo(**{**info, 'dbname': name})
    report = dict(database=name,queue_rows=575,historical_evidence_rows=5312,
                  transaction_limit_ms=250,cleanup_verified=False,
                  production_source_sha256=hashlib.sha256(Path(storage.__file__).read_bytes()).hexdigest())
    created = False
    scope = ExitStack()
    original_env = {key:os.environ.get(key) for key in ('TEMPO_DATABASE_WRITE_URL','TEMPO_DATABASE_READ_URL')}
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
            created = True
            admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name),sql.Literal(marker)))
        apply_migrations(fixture_dsn)
        postgres_store.close_pools()
        os.environ['TEMPO_DATABASE_WRITE_URL'] = fixture_dsn
        os.environ['TEMPO_DATABASE_READ_URL'] = fixture_dsn
        scope.enter_context(owned_admission_scope(name))
        day = date.today().isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with postgres_store.connection() as db:
            db.execute('INSERT INTO settings(id) VALUES(1)')
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('proof','Proof','synthetic',?)",(now,))
            # Deliberately preserve the empty-queue estimate; this is fixture-local.
            db.execute_native('ANALYZE daily_queue')
            db.execute_native('ANALYZE cards')
            db.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                      content_type,introduced_at,trained_color)
                SELECT 'card-'||lpad(n::text,4,'0'),'proof','prefix',%s,'["e2e4"]','learning',%s,
                       CASE WHEN n>=504 THEN 'opening' ELSE 'tactic' END,%s,'white'
                FROM generate_series(0,574) n""",(chess.STARTING_FEN,day,day))
            db.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval)
                SELECT id,'correct',%s,0,1 FROM cards WHERE content_type='tactic'""",(now,))
            db.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id)
                SELECT %s,id,substring(id from 6)::integer,
                    CASE WHEN content_type='opening' THEN 'new' ELSE 'review' END,'proof'
                FROM cards ORDER BY id""",(day,))
            db.execute_native("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id='card-0000'")
            db.execute_native("INSERT INTO teaching_states(card_id,revision,ply,taught_at) VALUES('card-0504',1,0,%s)",(now,))
            db.execute_native("""INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,presentation_snapshot_id,
                  repertoire_id,card_id,card_revision,trained_color,context_json,manifest_json,started_at,
                  study_timezone,queue_entry_id,state)
                SELECT 'historical-'||n,'synthetic-history',p.id,'proof',p.card_id,p.revision,'white',
                  json_build_object('synthetic',(SELECT string_agg(md5(n::text||':'||b::text),'') FROM generate_series(1,16) b))::text,
                  json_build_object('synthetic',(SELECT string_agg(md5(n::text||':'||b::text),'') FROM generate_series(17,46) b))::text,
                  %s,'UTC',CASE WHEN n=1 THEN NULL ELSE -n END,'complete'
                FROM generate_series(1,5312) n CROSS JOIN LATERAL
                  (SELECT id,card_id,revision FROM opening_evidence_presentations ORDER BY id LIMIT 1) p""",(now,))
            db.execute_native("""INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,presentation_snapshot_id,
                  repertoire_id,card_id,card_revision,trained_color,context_json,manifest_json,started_at,
                  study_timezone,queue_entry_id,state)
                SELECT 'current-protected','synthetic-current',p.id,'proof',p.card_id,p.revision,'white','{}','{}',
                  %s,'UTC',q.id,'complete'
                FROM opening_evidence_presentations p CROSS JOIN daily_queue q
                WHERE p.card_id='card-0505' AND q.card_id=p.card_id""",(now,))
        with postgres_store.connection(read_only=True) as db:
            rows = refresh.queue_application.presentation_rows(db,day)
            protected = queue_ordering._protected_queue_ids(rows)
            assert len(rows)==575 and len(protected)==3, (len(rows),len(protected))
            positions = {r['id']:r['position'] for r in rows if r['id'] in protected}
            history = [tuple(r) for r in db.execute_native('SELECT * FROM reviews ORDER BY id').fetchall()]
            origins = [tuple(r) for r in db.execute_native('SELECT * FROM queue_attempt_origins ORDER BY queue_entry_id,card_id,revision').fetchall()]
        plan = refresh._prepare_queue_randomization(day)
        assert plan['entries'] is not None
        # Actual issuance must invalidate the plan, with no receipt publication.
        with postgres_store.connection() as db:
            db.execute_native("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id='card-0008'")
        with postgres_store.connection(background=True) as db:
            assert refresh._publish_queue_randomization(db,day,plan) is False
        with postgres_store.connection() as db:
            assert db.execute('SELECT count(*) FROM daily_queue_days').fetchone()[0]==0
            db.execute_native("UPDATE queue_attempt_origins SET issued_as_head=0 WHERE card_id='card-0008'")
        # Changed eligibility must also invalidate the plan; retained full derived-state fence.
        with postgres_store.connection() as db:
            db.execute_native("UPDATE cards SET archived=1 WHERE id='card-0009'")
        with postgres_store.connection(background=True) as db:
            assert refresh._publish_queue_randomization(db,day,plan) is False
        with postgres_store.connection() as db:
            assert db.execute('SELECT count(*) FROM daily_queue_days').fetchone()[0]==0
            db.execute_native("UPDATE cards SET archived=0 WHERE id='card-0009'")
        # The complete production publication transaction must commit under its existing cap.
        started=time.perf_counter()
        with postgres_store.connection(background=True) as db:
            assert refresh._publish_queue_randomization(db,day,plan) is True
        report['publication_commit_ms']=round((time.perf_counter()-started)*1000,2)
        with postgres_store.connection(read_only=True) as db:
            after=refresh.queue_application.presentation_rows(db,day)
            assert len(after)==575 and {r['id'] for r in after}=={r['id'] for r in rows}
            assert {r['id']:r['position'] for r in after if r['id'] in protected}==positions
            assert [tuple(r) for r in db.execute_native('SELECT * FROM reviews ORDER BY id').fetchall()]==history
            assert [tuple(r) for r in db.execute_native('SELECT * FROM queue_attempt_origins ORDER BY queue_entry_id,card_id,revision').fetchall()]==origins
            assert db.execute('SELECT membership_hash FROM daily_queue_days').fetchone()[0].startswith(queue_ordering.PRESENTATION_VERSION+':')
            movable=[r for r in sorted(after,key=lambda r:(r['position'],r['id'])) if r['id'] not in protected]
            assert all(r['admission_kind']=='new' and r['content_type']=='opening' for r in movable[:69])
            assert all(not(r['admission_kind']=='new' and r['content_type']=='opening') for r in movable[69:])
        replay=refresh._prepare_queue_randomization(day)
        assert replay['entries'] is None
        with postgres_store.connection(background=True) as db:
            assert refresh._publish_queue_randomization(db,day,replay) is True
        report.update(complete=True,protected_positions_preserved=True,history_origins_preserved=True,
                      issuance_and_eligibility_stale_plans_rejected=True,replay_stable=True)
    finally:
        scope.close()
        postgres_store.close_pools()
        for key,value in original_env.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        if created:
            with psycopg.connect(admin_dsn,autocommit=True) as admin:
                assert admin.execute('SELECT shobj_description(oid,\'pg_database\') FROM pg_database WHERE datname=%s',(name,)).fetchone()[0]==marker
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
                assert admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone() is None
        report['cleanup_verified']=True
        print('PUBLICATION_PROTECTION',json.dumps(report,sort_keys=True),flush=True)
    print('PASS test_postgres_queue_publication_with_historical_and_null_evidence', flush=True)


if __name__ == '__main__':
    test_postgres_queue_publication_with_historical_and_null_evidence('postgresql://postgres@postgres:5432/tempo')
    test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound('postgresql://postgres@postgres:5432/tempo')
