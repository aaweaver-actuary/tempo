"""Existing source claim/publication workflow; no network or private-file access."""


def claim_source(transaction,clock,lease_id):
    with transaction() as operations:
        control=operations.control()
        now=clock()
        if not control or (control["next_attempt_at"] and control["next_attempt_at"]>now) or (control["leased_until"] and control["leased_until"]>now):
            return None
        cache=operations.cache()
        if not cache:
            return None
        credential=operations.credential_status()
        if credential["status"] not in ("registered","accepted"):
            operations.wait_for_credentials(credential,cache)
            return None
        lease=lease_id()
        row=operations.lease_cache(lease,cache)
        operations.lease_provider(lease)
        row["credential"]=credential
        return row


def publish_source(claim,payload,transaction,retrieval_id,clock):
    with transaction() as operations:
        # Provider control is always locked before a cache, matching claim order.
        operations.lock()
        cache=operations.fenced(claim)
        if not cache:
            return False
        operations.append(cache,{**payload,"retrieval_id":retrieval_id(),"retrieved_at":clock().isoformat()})
        credential=claim["credential"]
        operations.accept_credential(credential)
        operations.release(claim)
        return True
