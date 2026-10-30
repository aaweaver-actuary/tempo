"""Bounded Lichess history when the provider sends more rows than requested."""
import json
import httpx
import pytest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from uuid import uuid4

from test_product_continuous_sync import START, PGN, configuration, provider_fetch


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_oversized_sorted_provider_response_advances_without_loss(same_timestamp):
    rows = [dict(id=f"game{i:04}", createdAt=START*1000+(0 if same_timestamp else i),
        speed="rapid", rated=True, pgn=PGN) for i in range(205)]
    def provider(request):
        selected=[r for r in rows if int(request.url.params["since"]) <= r["createdAt"] < int(request.url.params["until"])]
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type":"application/x-ndjson"})
    continuation=None
    found=[]
    for _ in range(10):
        page=provider_fetch("lichess",provider,configuration().window,continuation)
        found.extend(r.source_game_id for r in page.records)
        if page.complete:
            break
        assert page.continuation != continuation
        continuation=page.continuation
    else:
        pytest.fail("oversized response failed to make bounded progress")
    assert len(found)==len(set(found))==205
    assert set(found)=={row["id"] for row in rows}


def test_oversized_response_with_out_of_order_tail_fails_closed():
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    rows=[dict(id=f"game{i:04}", createdAt=START*1000+i,
        speed="rapid",rated=True,pgn=PGN) for i in range(102)]
    rows[-1]["createdAt"]=START*1000
    def provider(request):
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in rows),
            headers={"Content-Type":"application/x-ndjson"})
    with pytest.raises(LichessAcquisitionError,match="invalid_response"):
        provider_fetch("lichess",provider,configuration().window)


def test_oversized_response_with_conflicting_tail_identity_fails_closed():
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    rows=[dict(id=f"game{i:04}",createdAt=START*1000+i,
        speed="rapid",rated=True,pgn=PGN) for i in range(102)]
    rows[-1]["id"]=rows[0]["id"]
    def provider(request):
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in rows),
            headers={"Content-Type":"application/x-ndjson"})
    with pytest.raises(LichessAcquisitionError,match="changed_boundary"):
        provider_fetch("lichess",provider,configuration().window)


def test_oversized_filtered_rows_are_bounded_and_accounted():
    rows=[dict(id=f"game{i:04}",createdAt=START*1000+i,
        speed="classical",rated=True,pgn=PGN) for i in range(205)]
    def provider(request):
        selected=[r for r in rows if int(request.url.params["since"]) <= r["createdAt"] < int(request.url.params["until"])]
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type":"application/x-ndjson"})
    continuation=None
    rejected=[]
    for _ in range(10):
        page=provider_fetch("lichess",provider,configuration().window,continuation)
        assert not page.records and len(page.rejections)<=100
        rejected.extend(item.source_game_id for item in page.rejections)
        if page.complete: break
        assert page.continuation != continuation
        continuation=page.continuation
    else: pytest.fail("filtered rows did not make bounded progress")
    assert len(rejected)==len(set(rejected))==205


def test_status_handles_replayed_page_count_without_hiding_missing_completed_page():
    from test_game_history_scripts import setup_scope, rows, collect
    from app.product_composition.game_history_scripts import report
    store,configuration,manifest,review=setup_scope()
    collect(store,configuration,manifest,review,records=rows(tuple("d4" for _ in range(101))),maximum_units=1)
    job=store.load_job(manifest.job(configuration).job_id)
    assert job.status=="queued" and job.continuation
    # A saved-page recovery was committed twice, but only one distinct input
    # page exists; the next cursor is pending, not evidence of lost data.
    store.save_job(job.model_copy(update={"pages_committed":2}))
    status=report(store,review,manifest)
    assert not status["complete"] and status["unique_games"]==100
    job=store.load_job(job.spec.job_id)
    store.save_job(job.model_copy(update={"status":"complete","window_complete":True}))
    with pytest.raises(ValueError,match="historical_page_missing"):
        report(store,review,manifest)


from test_product_game_lapse_postgres import owned_postgres


@pytest.mark.parametrize("blocker",["none","configuration","committed_cursor"])
def test_native_provider_limit_retry_fences_job_and_preserves_failure_receipt(owned_postgres,blocker,monkeypatch):
    from app.product_composition.game_history_scripts import new_manifest,report,retry_lichess_provider_limit
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from test_product_feedback_jobs import FakeClock
    from app.product_composition import game_history_scripts as history
    from test_game_history_scripts import setup_scope,rows,collect
    actual_policy=history.configuration_policy
    monkeypatch.setattr(history,"configuration_policy",lambda value: actual_policy(value).model_copy(update={"page_size":2}))
    @contextmanager
    def connections():
        with owned_postgres() as database:
            database.raw.execute("SET LOCAL transaction_timeout = '250ms'")
            yield database.raw
    store=PostgresCanonicalGamesStore(connections)
    _,configuration,_,_=setup_scope()
    configuration=configuration.model_copy(update={"workspace_id":"fixture-"+uuid4().hex})
    manifest=new_manifest(configuration.workspace_id,(configuration,),now=datetime(2026,10,30,tzinfo=timezone.utc))
    review=PostgresReviewStore(connections)
    review.configure(configuration,0)
    entries=rows(tuple("d4" for _ in range(3)))
    for row in entries: row["id"]="game-"+uuid4().hex
    def provider(request):
        selected=[r for r in entries if int(request.url.params["since"]) <= r["createdAt"] < int(request.url.params["until"])][:int(request.url.params["max"])]
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type":"application/x-ndjson"})
    transport=httpx.MockTransport(provider)
    assert collect(store,configuration,manifest,review,transport=transport,maximum_units=1)==1
    spec=manifest.job(configuration)
    checkpoint=store.load_job(spec.job_id)
    assert checkpoint.status=="queued" and checkpoint.continuation and checkpoint.last_checkpoint_at
    claimed=store.claim_job(FakeClock().now(),FakeClock().now()+120,job_id=spec.job_id)
    store.save_job(claimed.model_copy(update={"status":"failed","error":"lichess_response_limit","lease_until":0}))
    failed=store.load_job(spec.job_id)
    assert failed.status=="failed" and failed.continuation==checkpoint.continuation
    if blocker=="configuration":
        review.configure(configuration.model_copy(update={"revision":2}),1)
        with pytest.raises(ValueError,match="history_precision_saved_scope_changed"):
            store.resume_history_provider_limit(expected=failed,configuration=configuration,recorded_at=FakeClock().utc_now())
        assert store.load_job(spec.job_id)==failed
        return
    if blocker=="committed_cursor":
        with connections() as database:
            database.execute("""INSERT INTO product_feedback_pages(job_id,page_key,input_continuation,page_json)
                SELECT job_id,'already-committed',%s,jsonb_set(page_json,'{continuation}','null'::jsonb)
                FROM product_feedback_pages WHERE job_id=%s LIMIT 1""",
                (failed.continuation,spec.job_id))
        with pytest.raises(ValueError,match="history_provider_limit_cursor_already_committed"):
            store.resume_history_provider_limit(expected=failed,configuration=configuration,recorded_at=FakeClock().utc_now())
        assert store.load_job(spec.job_id)==failed
        return
    recovery=retry_lichess_provider_limit(manifest,store=store,review=review,admission=nullcontext,clock=FakeClock())
    assert recovery["prepared"] and recovery["receipt"]["reason"]=="lichess_response_limit"
    queued=store.load_job(spec.job_id)
    assert queued.status=="queued" and queued.continuation==failed.continuation
    assert queued.history_recoveries[0].prior_version==failed.version
    with pytest.raises(ValueError,match="history_provider_limit_job_changed"):
        store.resume_history_provider_limit(expected=failed,configuration=configuration,recorded_at=FakeClock().utc_now())
    assert collect(store,configuration,manifest,review,transport=transport)==1
    completed=store.load_job(spec.job_id)
    assert completed.status=="complete" and completed.window_complete
    assert completed.history_recoveries==queued.history_recoveries
    result=report(store,review,manifest)
    assert result["complete"] and result["unique_games"]==3
