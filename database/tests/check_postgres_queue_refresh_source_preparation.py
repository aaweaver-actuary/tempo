"""Task-owned N2 PG18 proof: run only during an exclusive native window."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timezone
from pathlib import Path
import json, os, sys, time, uuid
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'database'), str(ROOT/'backend'), str(ROOT/'backend/tests')]
from test_product_game_lapse_postgres import owned_postgres
from database.tests.position_study.italian_acceptance import seed_disposable_source, LINES, _moves
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters import postgres_store
from tempo_evidence_math.position_learning import Hold
from app.services import postgres_queue_refresh as refresh, durable_tasks, position_queue_admission as position
from app.product_composition.database_infrastructure import bind_database_infrastructure


def test_position_due_snapshot_and_writer_fences(owned_postgres, monkeypatch):
    bind_database_infrastructure()
    opts=owned_postgres.connection_options
    name='tempo_position_test_n2_snapshot_'+uuid.uuid4().hex[:12]
    with psycopg.connect(**opts,autocommit=True) as admin:
        for role in ('tempo_reader','tempo_writer'):
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(opts['password'])))
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    target={**opts,'dbname':name}
    dsn=make_conninfo(**target)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL',dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL',dsn)
    postgres_store.prepare_authoritative_background_pool()
    try:
        with psycopg.connect(**target,row_factory=tempo_row_factory) as raw:
            db,source,physical,day=seed_disposable_source(raw,dsn,(_moves(LINES['knight']),_moves(LINES['giuoco'])),('knight','giuoco'),repertoire_id='source-proof',now=datetime.now(timezone.utc))
            before_grades=raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0]
            before_states=raw.execute('SELECT jsonb_agg(to_jsonb(s) ORDER BY position) FROM study_position_states s').fetchone()[0]
            from app.services.queue_application import request_queue_refresh_in_transaction
            task=request_queue_refresh_in_transaction(db,day)
            epoch=raw.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s',(day,)).fetchone()[0]
            payload={'queue_date':day,'_queue_phase':'admit_position_due','_refresh_cycle_epoch':epoch}
            raw.execute("UPDATE background_tasks SET phase='admit_position_due',payload_json=%s WHERE id=%s",(json.dumps(payload),task['id']))
            raw.commit()
        claimed=durable_tasks.claim_task('daily_queue')
        assert claimed
        snapshots=[]; writes=[]; active=[False]; in_background=[False]; proposals=[]; foreground=[]
        held, release = Event(), Event()
        gate_orig=refresh.activity_gate.background_database_section
        @contextmanager
        def observed_gate():
            with gate_orig():
                in_background[0]=True
                try: yield
                finally: in_background[0]=False
        source_prepare=position.prepare_due
        source_admit=position.admit_due
        read_orig=refresh.read_connection if hasattr(refresh,'read_connection') else None
        def checked_prepare(db,day,payload):
            assert not in_background[0], 'unreserved snapshot entered background admission section'
            settings=tuple(db.execute_native("SELECT current_setting('transaction_read_only'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone())
            held.set()
            assert release.wait(10), 'foreground did not complete while snapshot was held'
            db.execute_native('SELECT pg_sleep(0.32)')
            start=time.perf_counter(); proposal=source_prepare(db,day,payload)
            snapshots.append((settings,round((time.perf_counter()-start)*1000,1)))
            proposals.append(proposal)
            assert isinstance(proposal,dict) and 'candidate' in proposal, proposal
            return proposal
        def checked_admit(db,day,proposal):
            assert not active[0], 'snapshot must be closed before writer'
            settings=tuple(db.execute_native("SELECT current_setting('transaction_read_only'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone())
            writes.append(settings)
            return source_admit(db,day,proposal)
        @contextmanager
        def counted_read(*a,**kw):
            assert kw.get('repertoire_snapshot') is True
            active[0]=True
            try:
                with read_orig(*a,**kw) as connection:
                    yield connection
            finally:
                active[0]=False
        patches=[patch.object(position,'prepare_due',checked_prepare),patch.object(position,'admit_due',checked_admit)]
        if read_orig is not None: patches.append(patch.object(refresh,'read_connection',counted_read))
        with patch.object(refresh.activity_gate,'background_database_section',observed_gate), patches[0],patches[1],(patches[2] if len(patches)>2 else __import__('contextlib').nullcontext()):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(refresh.execute_postgres_queue_refresh_slice, claimed)
                assert held.wait(10), 'source snapshot did not start'
                assert active[0] and not in_background[0]
                started = time.perf_counter()
                from app.services.queue_application import request_queue_refresh_in_transaction
                with postgres_store.connection() as foreground_db:
                    request_queue_refresh_in_transaction(foreground_db, day)
                foreground.append(round((time.perf_counter()-started)*1000,1))
                assert not future.done(), 'snapshot unexpectedly finished during foreground write'
                release.set()
                assert future.result(timeout=20)
        assert snapshots[0][0] == ('on','0','100ms')
        assert writes == [('off','250ms','25ms')]
        with psycopg.connect(**target,row_factory=tempo_row_factory) as raw:
            assert raw.execute('SELECT count(*) FROM daily_queue WHERE queue_date=%s AND status=\'queued\'',(day,)).fetchone()[0]==1
            assert {r['id']:r['fsrs_card_json'] for r in raw.execute('SELECT id,fsrs_card_json FROM cards')} == physical
            assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == before_grades
            assert raw.execute('SELECT jsonb_agg(to_jsonb(s) ORDER BY position) FROM study_position_states s').fetchone()[0] == before_states
            raw.execute("UPDATE study_position_owners SET mode='paused' WHERE repertoire_id='source-proof'")
            raw.commit()
            with postgres_store.connection(background=True) as writer:
                owner_hold=source_admit(writer,day,proposals[0])
                assert isinstance(owner_hold,Hold), owner_hold
            raw.execute("UPDATE study_position_owners SET mode='active' WHERE repertoire_id='source-proof'")
            raw.execute("UPDATE cards SET revision=revision+1 WHERE id='card-0'")
            raw.commit()
            with postgres_store.connection(background=True) as writer:
                source_hold=source_admit(writer,day,proposals[0])
                assert isinstance(source_hold,Hold), source_hold
            assert raw.execute('SELECT count(*) FROM daily_queue WHERE queue_date=%s',(day,)).fetchone()[0] == 1
        print('PASS real 320ms snapshot; reader',snapshots,'writer',writes,'foreground_ms',foreground,'stale_owner',owner_hold,'stale_source',source_hold)
    finally:
        postgres_store.close_pools()
