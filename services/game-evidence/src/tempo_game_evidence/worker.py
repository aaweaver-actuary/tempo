"""Existing comparison worker control flow; persistence remains with its store."""
from .reconciliation import resume_reconciliation


def process_comparison_job(store, *, before_publish=None):
    job = store.claim_job()
    if not job:
        return False
    try:
        if job["kind"] == "rescope":
            store._rescope(job)
        elif job["kind"] == "reconcile":
            resume_reconciliation(store, job)
        else:
            store._compare(job, before_publish)
    except Exception:
        store.record_job_failure(job)
        raise
    return True
