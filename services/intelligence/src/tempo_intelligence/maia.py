"""Registered-job construction and validation, without model execution."""

import chess
from tempo_analysis_jobs_contracts.v1 import MaiaJob, MaiaResult

from .identity import canonical_position, digest


def build_maia_job(descriptor: dict, workspace: str, cache_id: str, work_generation: int, execution_generation: int) -> MaiaJob:
    if descriptor.get("kind") != "maia":
        raise ValueError("wrong_source_kind")
    if not descriptor.get("legal_moves"):
        raise ValueError("terminal_position")
    try:
        terminal = descriptor.get("terminal_reason") or chess.Board(descriptor["canonical_fen"]).is_game_over(claim_draw=False)
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_maia_descriptor") from None
    if terminal:
        raise ValueError("terminal_position")
    try:
        return MaiaJob.model_validate({
            "contract_version": 1,
            "workspace_id": workspace,
            "job_id": "maia:" + digest({"workspace": workspace, "cache_id": cache_id}).split(":", 1)[1],
            "request_revision": work_generation,
            "execution_generation": execution_generation,
            "input_digest": digest(descriptor),
            "constraints": {"max_wall_time_ms": 5000, "max_memory_mb": 512},
            "worker": "maia",
            "fen": descriptor["canonical_fen"],
            "rating": descriptor["rating"],
            "model_id": descriptor["model_id"],
            "model_version": descriptor["model_version"],
            "legal_moves": descriptor["legal_moves"],
        })
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_maia_descriptor") from None


def validate_maia_result(job: MaiaJob, result: MaiaResult) -> None:
    """Validate shared wire semantics plus the requesting owner's exact legal inputs."""
    try:
        job = MaiaJob.model_validate(job.model_dump(mode="json"))
        result = MaiaResult.model_validate(result.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid_maia_payload") from None
    if (job.workspace_id, job.job_id, job.request_revision, job.execution_generation, job.input_digest) != (result.workspace_id, result.job_id, result.request_revision, result.execution_generation, result.provenance.input_digest):
        raise ValueError("maia_identity_mismatch")
    if (job.model_id, job.model_version) != (result.model_id, result.model_version):
        raise ValueError("maia_model_mismatch")
    try:
        _, canonical_fen = canonical_position(job.fen)
        legal_moves = {move.uci() for move in chess.Board(canonical_fen).legal_moves}
    except (TypeError, ValueError):
        raise ValueError("invalid_maia_position") from None
    if set(job.legal_moves) != legal_moves:
        raise ValueError("maia_legal_universe_mismatch")
    if {prediction.move for prediction in result.predictions} != set(job.legal_moves):
        raise ValueError("maia_prediction_moves_mismatch")
