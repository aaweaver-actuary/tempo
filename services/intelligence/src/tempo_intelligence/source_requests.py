"""Request descriptor identity and cache retry decisions; no database access."""
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
