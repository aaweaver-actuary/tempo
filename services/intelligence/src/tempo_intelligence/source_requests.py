"""Source request admission, inspection and cache decisions; no database access."""
from datetime import timedelta
from .errors import DomainError
from .identity import digest, source_descriptor


def request_descriptors(request,configuration):
    descriptors=[source_descriptor(request.snapshot,source) for source in request.sources]
    for descriptor in descriptors:
        if descriptor["kind"] != "maia":
            descriptor["provider_origin"]=configuration.provider_base_url
    return descriptors


def cache_identity(workspace_id,descriptor):
    return digest({"workspace":workspace_id,"descriptor":{key:value for key,value in descriptor.items() if key != "legal_universe_digest"}})


def retry_eligible(cache,now,configuration):
    return bool(cache["retryable"] and cache["state"] in ("failed","waiting_credentials","retry_wait")
        and (cache["next_attempt_at"] is None or cache["next_attempt_at"]<=now)
        and (cache["kind"] == "maia" or cache["descriptor"]["provider_origin"] == configuration.provider_base_url))


def admit_request(request,workspace_id):
    if request.snapshot.workspace_id != workspace_id:
        raise DomainError("workspace_mismatch",403)
    payload=request.model_dump(mode="json")
    return payload


def validate_request_revision(request,head):
    expected=request.expected_revision
    if (head and (head["revision"] != expected or request.request_revision <= head["revision"])) or (not head and expected not in (None,0)):
        raise DomainError("request_revision_conflict")


def request_snapshot(request):
    snapshot=request.snapshot.model_dump(mode="json")
    immutable_snapshot={key:value for key,value in snapshot.items() if key != "policy_context"}
    snapshot_identity=digest({key:snapshot[key] for key in ("source_id","source_revision","snapshot_id","publication_id","publication_generation")})
    return snapshot,immutable_snapshot,snapshot_identity


def source_inspection(cache,evidence,request,retry,clock):
    freshness="absent" if not evidence else "current" if evidence["accepted_at"]+timedelta(seconds=request["payload"]["max_age_seconds"])>clock() else "stale"
    source={"kind":cache["kind"],"cache_id":cache["cache_id"],"work_generation":cache["generation"],"execution_generation":cache["execution_generation"],"state":cache["state"],"freshness":freshness,"evidence_id":cache["accepted_id"],"error_code":cache["error_code"],"attempts":cache["attempts"],"retryable":cache["retryable"],"retry_at":cache["next_attempt_at"],"retry_eligible":retry(cache,clock())}
    return source


def failure_transition(cache,code,retryable,retry_at,auth_failed,cancelled,clock):
    waiting=code.startswith("credential_") or code.startswith("session_") or auth_failed
    attempts=cache["attempts"]+(0 if retry_at or waiting or cancelled else 1)
    state="queued" if cancelled else "waiting_credentials" if waiting else "retry_wait" if retry_at or (retryable and attempts<3) else "failed"
    deadline=retry_at or (clock()+timedelta(seconds=60 if waiting else (5 if attempts<2 else 30)) if state in ("retry_wait","waiting_credentials") else None)
    can_retry=bool(retryable or waiting or retry_at or cancelled)
    return attempts,state,deadline,can_retry
