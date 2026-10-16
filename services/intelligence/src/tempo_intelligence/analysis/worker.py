"""Bounded dispatcher. The external pilot supplies B's installed public client."""
from ..errors import DomainError


def work(owner,run_id,maximum,profile=None,client=None):
    try:
        if type(maximum) is not int or not 1<=maximum<=1000:
            raise DomainError("invalid_analysis_work_bound",422)
        owner.derive(run_id)
        if client is None and profile is not None:
            raise DomainError("use_installed_pilot_for_engine_dispatch",422)
        if client is None:
            owner.mark_engine_unavailable(run_id)
            owner.derive(run_id)
            return {"schema_version":1,"workspace_id":owner.workspace,"engine":"unavailable_not_configured","jobs":0,"run":owner.status(run_id)}
        client.cancel_requested=lambda: owner.status(run_id)["state"]=="cancel_requested"
        completed=0
        for _ in range(maximum):
            identity=getattr(client,"identity",None)
            claim=owner.claim(run_id,client.build_job,identity) if identity is not None else owner.claim(run_id,client.build_job)
            if claim is None:
                break
            if claim.get("error") == "incompatible_engine_profile":
                owner.derive(run_id)
                raise DomainError("incompatible_engine_profile",409)
            try:
                result=client.execute(claim["job"])
                owner.accept(claim,result)
                completed+=1
            except (KeyboardInterrupt,SystemExit):
                # B's final acknowledgement precedes durable cancellation completion.
                client.cancel(claim["job"])
                owner.failure(claim,code="cancelled")
                raise
            except Exception as error:
                client.close()  # Establish session loss before releasing durable capacity.
                if str(error) in {"incompatible_engine_profile", "incompatible_engine_inputs"}:
                    owner.failure(claim,retryable=False,code="incompatible_engine_profile",deferred=True)
                    owner.derive(run_id)
                    raise DomainError("incompatible_engine_profile",409) from None
                retryable=getattr(error,"retryable",True)
                reason=getattr(error,"reason","worker_session_lost")
                code="cancelled" if reason=="cancelled" else "worker_busy" if reason in {"worker_busy","worker_not_ready"} else "execution_failed" if hasattr(error,"retryable") else "worker_session_lost"
                owner.failure(claim,retryable=retryable,code=code,deferred=reason in {"worker_busy","worker_not_ready"},status_payload=getattr(error,"status_payload",None))
                raise DomainError("analysis_worker_failed",503) from None
            owner.derive(run_id)
        return {"schema_version":1,"workspace_id":owner.workspace,"jobs":completed,"engine_identity":getattr(client,"identity",None),"run":owner.status(run_id)}
    finally:
        if client is not None:client.close()
