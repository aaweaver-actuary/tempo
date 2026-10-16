"""Adapter to B's installed PUBLIC client. Importing Intelligence never loads B."""
import asyncio
import inspect
import os
import json
from pathlib import Path
from tempo_analysis_jobs_contracts import v1
from tempo_intelligence import analysis_v1
make_job=analysis_v1.make_stockfish_job

class DomainError(RuntimeError):
    def __init__(self,code,status=503):
        super().__init__(code)


class ExecutionFailure(RuntimeError):
    def __init__(self, retryable, reason, status_payload=None):
        self.retryable=retryable
        self.reason=reason
        self.status_payload=status_payload


class Adapter:
    def __init__(self,profile):
        try:
            from tempo_stockfish_client import StockfishClient, stockfish_input_digest
        except ImportError:
            raise DomainError("stockfish_client_not_installed",503) from None
        self.digest=stockfish_input_digest
        self.profile=profile
        self.runner=asyncio.Runner()
        if "env" not in inspect.signature(StockfishClient).parameters:
            self.runner.close()
            raise DomainError("stockfish_client_environment_boundary_required",503)
        environment={key:value for key,value in os.environ.items() if key in {"PATH","HOME","TMPDIR","TMP","TEMP","LANG"} or key.startswith("LC_")}
        self.client=StockfishClient(profile["command"],env=environment)
        try:
            self.runner.run(self.client.__aenter__())
            if self.client.identity.get("engine_version")!=profile["engine_version"]:
                raise ValueError("worker_profile_mismatch")
        except BaseException:
            self.close()
            raise DomainError("stockfish_profile_unavailable",503) from None
        self.identity=dict(self.client.identity)
        self.records=self.client.records()
        self.completed_requests=set()

    def build_job(self,position,run_id,revision,generation,stage,depth):
        job=make_job(position,run_id,revision,generation,stage,self.profile["engine_version"],self.profile["memory_mb"],depth)
        job["input_digest"]=self.digest(job)
        return v1.StockfishJob.model_validate(job).model_dump(mode="json")

    async def _execute(self,job):
        await self.client.send_job(v1.StockfishJob.model_validate(job))
        pending=None
        cancellation=None
        try:
            while True:
                if pending is None:pending=asyncio.create_task(anext(self.records))
                ready,_=await asyncio.wait({pending},timeout=0.2)
                check=getattr(self,"cancel_requested",None)
                if cancellation is None and check is not None and await asyncio.to_thread(check):
                    cancellation=v1.CancellationRequest(contract_version=1,workspace_id=job["workspace_id"],job_id=job["job_id"],request_revision=job["request_revision"],execution_generation=job["execution_generation"],cancellation_id="cancel:"+job["job_id"][-64:]+":"+str(job["execution_generation"]))
                    await self.client.send_cancellation(cancellation)
                if not ready:continue
                message=pending.result();pending=None
                identity=(message.workspace_id,message.job_id,message.request_revision,message.execution_generation)
                if identity!=(job["workspace_id"],job["job_id"],job["request_revision"],job["execution_generation"]):
                    # B emits completed status after a result; it may arrive at the next stage.
                    if isinstance(message,v1.JobStatus) and message.status=="completed" and identity in self.completed_requests:continue
                    raise ValueError("worker_response_identity_mismatch")
                if cancellation is not None:
                    if isinstance(message,v1.CancellationAcknowledgement) and message.cancellation_id==cancellation.cancellation_id and message.status in {"terminated","already_completed","stale_generation"}:
                        raise ExecutionFailure(False,"cancelled")
                    continue
                if isinstance(message,v1.StockfishResult):
                    if any(getattr(message.provenance,key)!=self.client.identity.get(key) for key in ("runtime_name","runtime_version","implementation_version")):
                        raise ValueError("worker_provenance_mismatch")
                    self.completed_requests.add(identity)
                    return message.model_dump(mode="json")
                if isinstance(message,v1.JobStatus) and message.status in {"failed","cancelled"}:raise ExecutionFailure(message.retryable,message.reason,message.model_dump(mode="json"))
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)

    def execute(self,job):
        return self.runner.run(asyncio.wait_for(self._execute(job),45))

    async def _cancel(self,job):
        request=v1.CancellationRequest(contract_version=1,workspace_id=job["workspace_id"],job_id=job["job_id"],request_revision=job["request_revision"],execution_generation=job["execution_generation"],cancellation_id="cancel:"+job["job_id"][-64:]+":"+str(job["execution_generation"]))
        await self.client.send_cancellation(request)
        async for message in self.client.records():
            if isinstance(message,v1.CancellationAcknowledgement) and message.cancellation_id==request.cancellation_id and message.status in {"terminated","already_completed","stale_generation"}:
                return message
        raise ExecutionFailure(True,"worker_session_lost")

    def cancel(self,job):
        return self.runner.run(asyncio.wait_for(self._cancel(job),10))

    def close(self):
        if self.runner is not None:
            try:
                self.runner.run(self.client.close())
            finally:
                self.runner.close();self.runner=None


def connect(path):
    try:
        raw=Path(path).read_bytes()
        if len(raw)>65536:
            raise ValueError()
        profile=json.loads(raw)
        if set(profile)!={"schema_version","command","engine_version","memory_mb"} or type(profile["schema_version"]) is not int or profile["schema_version"]!=1 or not isinstance(profile["command"],list) or not profile["command"] or any(not isinstance(arg,str) or not arg for arg in profile["command"]) or type(profile["memory_mb"]) is not int or profile["memory_mb"]!=512:
            raise ValueError()
    except Exception:
        raise DomainError("invalid_stockfish_profile",422) from None
    return Adapter(profile)
