"""Typed local transport sequencing; fresh installed worker proof stays separate."""
import asyncio
import pytest
from tempo_analysis_jobs_contracts import v1
from tempo_analysis_pilot.stockfish_client import Adapter


def job(identity):
    return v1.StockfishJob(contract_version=1,workspace_id='fixture',job_id=identity,request_revision=1,execution_generation=1,input_digest='synthetic-digest',constraints={'max_wall_time_ms':10000,'max_memory_mb':512},worker='stockfish',fen='7k/5Q2/6K1/8/8/8/8/8 w - - 0 1',moves=[],search_moves=[],max_depth=20,max_nodes=None,multi_pv=1,engine_version='synthetic-profile').model_dump(mode='json')


def result(request):
    return v1.StockfishResult(contract_version=1,**{key:request[key] for key in ('workspace_id','job_id','request_revision','execution_generation','worker','engine_version')},provenance={'input_digest':request['input_digest'],'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test','started_at':'2026-10-16T00:00:00Z','finished_at':'2026-10-16T00:00:01Z'},variations=[{'rank':1,'moves':['f7g7'],'score':{'kind':'mate','value':1},'score_perspective':'side_to_move','depth':20}])


class Transport:
    identity={'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test'}
    async def send_job(self,request):pass


def test_trailing_completed_status_does_not_reject_the_next_search():
    root,played=job('root'),job('played')
    async def records():
        yield result(root)
        yield v1.JobStatus(contract_version=1,**{key:root[key] for key in ('workspace_id','job_id','request_revision','execution_generation')},status='completed',retryable=False,reason=None)
        yield result(played)
    adapter=Adapter.__new__(Adapter);adapter.client=Transport();adapter.records=records();adapter.completed_requests=set()
    async def prove():
        assert (await adapter._execute(root))['job_id']=='root'
        assert (await adapter._execute(played))['job_id']=='played'
    asyncio.run(prove())


def test_unknown_terminal_status_cannot_bypass_execution_identity():
    requested=job('current')
    async def records():
        yield v1.JobStatus(contract_version=1,workspace_id='fixture',job_id='foreign',request_revision=1,execution_generation=1,status='completed',retryable=False,reason=None)
    adapter=Adapter.__new__(Adapter);adapter.client=Transport();adapter.records=records();adapter.completed_requests=set()
    with pytest.raises(ValueError,match='worker_response_identity_mismatch'):asyncio.run(adapter._execute(requested))


def test_result_provenance_must_match_the_ready_worker_identity():
    requested=job('current')
    async def records():
        yield result(requested).model_copy(update={'provenance':result(requested).provenance.model_copy(update={'implementation_version':'foreign'})})
    adapter=Adapter.__new__(Adapter);adapter.client=Transport();adapter.records=records();adapter.completed_requests=set()
    with pytest.raises(ValueError,match='worker_provenance_mismatch'):asyncio.run(adapter._execute(requested))
