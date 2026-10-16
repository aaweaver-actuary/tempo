"""Result/cancellation consumer illustration; no computation is started."""

from tempo_analysis_jobs_contracts import v1


def accept_result(job, result, *, current_generation, cancellation_status=None):
    if type(job) is v1.StockfishJob and type(result) is not v1.StockfishResult:
        raise ValueError("wrong worker result")
    if type(job) is v1.MaiaJob and type(result) is not v1.MaiaResult:
        raise ValueError("wrong worker result")
    expected = (
        job.workspace_id,
        job.job_id,
        job.request_revision,
        current_generation,
        job.input_digest,
    )
    received = (
        result.workspace_id,
        result.job_id,
        result.request_revision,
        result.execution_generation,
        result.provenance.input_digest,
    )
    if (
        expected != received
        or job.execution_generation != current_generation
        or cancellation_status == "terminated"
    ):
        raise ValueError("stale result")
    if isinstance(job, v1.StockfishJob):
        if (
            result.engine_version != job.engine_version
            or len(result.variations) > job.multi_pv
            or any(
                job.search_moves and variation.moves[0] not in job.search_moves
                for variation in result.variations
            )
        ):
            raise ValueError("result outside Stockfish request")
    elif (
        result.model_id != job.model_id
        or result.model_version != job.model_version
        or set(item.move for item in result.predictions) != set(job.legal_moves)
    ):
        raise ValueError("result outside Maia request")
    return result


def computation_terminated(request, acknowledgement):
    identity_fields = (
        "workspace_id",
        "job_id",
        "request_revision",
        "execution_generation",
        "cancellation_id",
    )
    if any(
        getattr(request, field) != getattr(acknowledgement, field)
        for field in identity_fields
    ):
        raise ValueError("cancellation acknowledgement identity mismatch")
    return acknowledgement.status == "terminated"


def receive_job(existing_requests, job):
    """Illustrate stable request identity independently of execution retries."""
    key = (job.workspace_id, job.job_id, job.request_revision)
    semantic_payload = job.model_dump(exclude={"execution_generation"})
    if key in existing_requests and existing_requests[key] != semantic_payload:
        raise ValueError("job request identity payload conflict")
    existing_requests[key] = semantic_payload
    return key
