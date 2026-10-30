"""Chess.com daily mapping and guarded recovery of a quarantined history page."""
import asyncio
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
from threading import Event
from uuid import uuid4

import httpx
import pytest

from test_product_game_lapse_postgres import owned_postgres
from test_product_continuous_sync import archive_row, archive_http


def test_daily_mapping_uses_original_provider_fact():
    from test_product_source_identity import inputs
    from tempo_legacy_database.adapters.games.product_sources import _prepare
    record, spec, _ = inputs(provider='chesscom')
    record = record.model_copy(update={'source_metadata':(('rated','true'),('time_class','"daily"'))})
    assert _prepare(record, spec).speed == 'classical'
    assert dict(record.source_metadata)['time_class'] == '"daily"'


@pytest.mark.parametrize('blocker', ['none', 'configuration', 'delivery', 'source_changed', 'wide', 'replay_retry', 'new_canonical', 'new_write_failure'])
def test_daily_history_recovery_keeps_original_evidence_and_replays_exact_page(owned_postgres, monkeypatch, blocker):
    from app.product_composition.game_history_scripts import (new_manifest, recover_chesscom_daily,
        run_history, report, DeferredSnapshots, configuration_policy)
    from app.product_composition.game_sync import create_game_sync, WorkflowNormalizer
    from app.games.providers.chesscom_acquisition import fetch_chesscom_page
    from app.games.feedback.interfaces import stable_id
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore, _prepare, _values
    from tempo_legacy_database.adapters.games.sync.records import insert_imported_game
    from test_game_history_scripts import setup_scope
    from test_product_feedback_jobs import FakeClock
    limited = blocker != 'wide'
    @contextmanager
    def connections():
        with owned_postgres() as db:
            if limited:
                db.raw.execute("SET LOCAL transaction_timeout = '250ms'")
            yield db.raw
    store=PostgresCanonicalGamesStore(connections)
    _, config, _, _ = setup_scope()
    config=config.model_copy(update={'workspace_id':'fixture-'+uuid4().hex,'provider':'chesscom',
        'account':'explicitaccount','speeds':('rapid','classical'),'rated':None})
    manifest=new_manifest(config.workspace_id,(config,),now=datetime(2026,10,30,tzinfo=timezone.utc))
    spec=manifest.job(config)
    review=PostgresReviewStore(connections)
    review.configure(config,0)
    suffix=uuid4().hex
    data=[archive_row('a'+suffix),archive_row('b'+suffix,time_class='daily'),archive_row('c'+suffix)]
    if blocker == 'wide':
        data=[archive_row('a'+suffix),archive_row('b'+suffix,time_class='daily')]+[
            archive_row(f'c{i:03}'+suffix) for i in range(100)]
    transport=httpx.MockTransport(archive_http({'10':data}))
    class Pages:
        def fetch_page(self, *, account, window, continuation=None):
            async def fetch():
                async with httpx.AsyncClient(transport=transport) as client:
                    return await fetch_chesscom_page(account,window,client,continuation=continuation,
                        speeds=config.speeds,rated_only=False,
                        max_page_records=100 if blocker == 'wide' and continuation else 1)
            return asyncio.run(fetch())
    def factory(c):
        runtime=create_game_sync(policies=(configuration_policy(c),),store=store,snapshots=DeferredSnapshots(),
            clock=FakeClock(),delivery_enabled=False,source_identity=store,encounter_feedback=True,
            preserve_existing_comparison=True)
        runtime.runner.coordinator.providers['chesscom']=Pages()
        return runtime.runner
    def run(maximum):
        return run_history(manifest,store=store,review=review,clock=FakeClock(),admission=nullcontext,
            stopped=Event(),runner_factory=factory,maximum_units=maximum)
    assert run(1)==1
    first=store.load_job(spec.job_id)
    assert first.continuation and first.status=='queued'
    page=Pages().fetch_page(account=spec.account,window=spec.window,continuation=first.continuation)
    record=page.records[0]
    assert dict(record.source_metadata)['time_class']=='"daily"'
    prepared=_prepare(record,spec)
    if blocker not in {'new_canonical','new_write_failure'}:
        with owned_postgres() as db:
            insert_imported_game(db,'chess.com:'+record.source_game_id,'chess.com',_values(prepared))
    original=store.bind_source
    def old_bind(game, *, record, spec):
        if record.source_game_id == page.records[0].source_game_id:
            raise ValueError('explicit_provider_speed_and_rated_facts_required')
        return original(game,record=record,spec=spec)
    with monkeypatch.context() as patch:
        patch.setattr(store,'bind_source',old_bind)
        assert run(1)==1
    # Seed the already-committed 100-record page without a timing assertion;
    # the live page exists. Apply the deployed cap to the recovery itself.
    limited = True
    failed=store.load_job(spec.job_id)
    assert failed.status=='quarantined' and failed.continuation==first.continuation
    key=stable_id(spec.workspace_id,spec.provider,record.source_game_id)
    before=store.source(key)
    with connections() as db:
        saved=db.execute('SELECT payload::text FROM product_feedback_captures WHERE source_key=%s',(key,)).fetchone()[0]
        if blocker=='delivery':
            db.execute("""INSERT INTO product_feedback_deliveries
                (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
                VALUES('existing',%s,'existing','encounter','pending',0,'{}'::jsonb)""",(key,))
        if blocker=='new_write_failure':
            db.execute("""CREATE FUNCTION reject_daily_update() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'daily_update_failure'; END $$""")
            db.execute("""CREATE TRIGGER reject_daily_update BEFORE UPDATE ON product_feedback_sources
                FOR EACH ROW EXECUTE FUNCTION reject_daily_update()""")
    if blocker=='configuration':
        review.configure(config.model_copy(update={'revision':2}),1)
        with pytest.raises(ValueError,match='history_daily_saved_scope_changed'):
            recover_chesscom_daily(manifest,store=store,review=review,admission=nullcontext)
        assert store.source(key)==before and store.load_job(spec.job_id)==failed
        return
    if blocker=='source_changed':
        verified=WorkflowNormalizer().normalize(record,spec)
        with pytest.raises(ValueError,match='history_daily_verified_source_mismatch'):
            store.recover_daily_history_page(expected=failed,configuration=config,page_document=page.model_dump_json(),
                record=record,verified=verified.model_copy(update={'played_at':'2026-10-28T00:00:00Z'}))
        assert store.source(key)==before and store.load_job(spec.job_id)==failed
        return
    if blocker=='delivery':
        with pytest.raises(ValueError,match='history_daily_delivery_exists'):
            recover_chesscom_daily(manifest,store=store,review=review,admission=nullcontext)
        assert store.source(key)==before and store.load_job(spec.job_id)==failed
        return
    if blocker=='new_write_failure':
        import psycopg
        try:
            with pytest.raises(psycopg.errors.RaiseException,match='daily_update_failure'):
                recover_chesscom_daily(manifest,store=store,review=review,admission=nullcontext)
            assert store.source(key)==before and store.load_job(spec.job_id)==failed
            with connections() as db:
                assert db.execute('SELECT count(*) FROM imported_games WHERE id=%s',
                    ('chess.com:'+record.source_game_id,)).fetchone()[0]==0
                assert db.execute('SELECT count(*) FROM product_feedback_captures WHERE source_key=%s',(key,)).fetchone()[0]==1
        finally:
            with connections() as db:
                db.execute('DROP TRIGGER reject_daily_update ON product_feedback_sources')
                db.execute('DROP FUNCTION reject_daily_update()')
        return
    assert recover_chesscom_daily(manifest,store=store,review=review,admission=nullcontext)=={
        'prepared':True,'page_records':len(page.records),'recovered':1}
    with connections() as db:
        captures=db.execute('SELECT payload::text FROM product_feedback_captures WHERE source_key=%s',(key,)).fetchall()
        assert len(captures)==2 and any(row[0]==saved for row in captures)
    assert not store.source(key).quarantined
    with connections() as db:
        assert db.execute('SELECT count(*) FROM imported_games WHERE id=%s',
            ('chess.com:'+record.source_game_id,)).fetchone()[0]==1
    if blocker == 'wide':
        limited = False  # Replay correctness is covered separately under the cap.
    if blocker == 'replay_retry':
        with monkeypatch.context() as patch:
            patch.setattr(store,'commit_page',lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
            assert run(1)==1
        retry=store.load_job(spec.job_id)
        assert retry.status=='retryable' and retry.history_replay_cursor==first.continuation
        store.save_job(retry.model_copy(update={'next_attempt_at':0}))
        old_fetch=Pages.fetch_page
        def no_refetch(self, *, account, window, continuation=None):
            assert continuation != first.continuation, 'saved replay must not refetch provider'
            return old_fetch(self,account=account,window=window,continuation=continuation)
        monkeypatch.setattr(Pages,'fetch_page',no_refetch)
    assert run(1)==1
    replayed=store.load_job(spec.job_id)
    assert replayed.status=='queued' and replayed.continuation==page.continuation
    assert run(10)==1
    result=report(store,review,manifest)
    assert result['complete'] and result['unique_games']==len(data) and not result['unavailable_or_quarantined_sources']
