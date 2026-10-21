"""Registered Maia work and evidence acceptance without model execution or SQL."""
from tempo_analysis_jobs_contracts.v1 import MaiaJob

from .errors import DomainError
from .maia import build_maia_job, validate_maia_result


def require_profile(descriptor,profiles,operations):
    configured=any(profile["model_id"]==descriptor["model_id"] and profile["model_version"]==descriptor["model_version"] and profile["artifact_sha256"]==descriptor["artifact_sha256"] and descriptor["rating"] in profile["ratings"] for profile in profiles())
    operations.require_profile(descriptor,configured)


def create_maia_job(cache,workspace,operations):
    execution=cache["execution_generation"]+1
    previous=operations.previous_job(cache)
    # Worker request contents, including constraints, are immutable within
    # a work generation. New defaults apply only to a new work generation.
    job=(MaiaJob.model_validate({**previous["payload"],"execution_generation":execution}) if previous else
         build_maia_job(cache["descriptor"],workspace(),cache["cache_id"],cache["generation"],execution))
    payload=job.model_dump(mode="json")
    operations.create_job(job,execution,cache,payload)


def maia_jobs(request_id,transaction):
    with transaction() as operations:
        request=operations.request(request_id)
        result=[]
        for binding in request["bindings"]:
            if binding["kind"] == "maia":
                rows=operations.pending_jobs(binding["cache_id"])
                result.extend(row["payload"] for row in rows)
        return {"schema_version":1,"jobs":result}


def accept_maia(result,workspace,transaction):
    if result.workspace_id != workspace():
        raise DomainError("workspace_mismatch",403)
    wire=result.model_dump(mode="json")
    with transaction() as operations:
        job=operations.job(result)
        if job["result_payload"] is not None:
            operations.verify_result_replay(job,result)
            return {"schema_version":1,"evidence_id":job["evidence_id"],"replayed":True}
        cache=operations.result_cache(job,result)
        try:
            validate_maia_result(MaiaJob.model_validate(job["payload"]),result)
        except ValueError:
            raise DomainError("maia_result_mismatch",422) from None
        evidence_id=operations.append(cache,{"schema_version":1,"source":"maia","job":job["payload"],"result":wire,"artifact_sha256":cache["descriptor"]["artifact_sha256"],"limitations":["v1_artifact_identity_is_configured_version_binding"]})
        operations.complete_job(wire,evidence_id,result)
        return {"schema_version":1,"evidence_id":evidence_id,"replayed":False}


def maia_status(status,workspace,transaction):
    if status.workspace_id != workspace():
        raise DomainError("workspace_mismatch",403)
    with transaction() as operations:
        job=operations.job(status)
        wire=status.model_dump(mode="json")
        if job["status_payload"] == wire:
            return {"schema_version":1,"status":status.status}
        cache=operations.status_cache(job,status)
        if status.status not in ("pending","running","failed","cancelled"):
            raise DomainError("verified_result_required",422)
        operations.update_status(job,status,wire)
        if status.status in ("failed","cancelled"):
            operations.fail_cache(status,cache)
        return {"schema_version":1,"status":status.status}
