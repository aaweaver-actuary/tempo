"""Shared Stockfish models only; optional B execution is loaded on explicit work."""
import chess
from tempo_analysis_jobs_contracts.v1 import StockfishJob, StockfishResult
from ..identity import digest
from .classify import compare_scores


def make_job(position, run_id, revision, execution_generation, stage, engine_version, memory_mb, depth=20):
    job = {"contract_version":1,"workspace_id":position["workspace_id"],"job_id":digest({"run":run_id,"revision":revision,"position":position["position_id"],"stage":stage}),
           "request_revision":revision,"execution_generation":execution_generation,"input_digest":"pending",
           "constraints":{"max_wall_time_ms":10000,"max_memory_mb":memory_mb},"worker":"stockfish",
           "fen":position["starting_fen"],"moves":position["history"],"search_moves":[] if stage=="root" else [position["actual_move"]],
           "max_depth":depth,"max_nodes":None,"multi_pv":1,"engine_version":engine_version}
    # B owns the executable digest convention; the dispatcher must use its public helper.
    return job


def validate_result(job, result):
    job = StockfishJob.model_validate(job)
    result = StockfishResult.model_validate(result)
    for key in ("workspace_id","job_id","request_revision","execution_generation","engine_version"):
        if getattr(job,key) != getattr(result,key):
            raise ValueError("engine_identity_mismatch")
    if result.provenance.input_digest != job.input_digest:
        raise ValueError("engine_digest_mismatch")
    board = chess.Board(job.fen)
    for move in job.moves:
        board.push_uci(move)
    if len(result.variations)!=1:
        raise ValueError("engine_variation_count_mismatch")
    for variation in result.variations:
        if job.max_depth is not None and variation.depth>job.max_depth:
            raise ValueError("engine_depth_exceeds_request")
        replay = board.copy()
        if job.search_moves and variation.moves[0] not in job.search_moves:
            raise ValueError("engine_restriction_mismatch")
        for move in variation.moves:
            replay.push_uci(move)
    return result.model_dump(mode="json")


def worker_identity(value):
    provenance = value.get("provenance", value)
    return (value.get("engine_version"), *(provenance.get(key) for key in
            ("runtime_name", "runtime_version", "implementation_version")))


def compatible_worker(root, identity):
    expected, actual = worker_identity(root), worker_identity(identity)
    return all(expected) and expected == actual


def validate_pair_jobs(root, played, actual_move):
    if (root["fen"], root["moves"], root["search_moves"], played["search_moves"]) != (played["fen"], played["moves"], [], [actual_move]):
        raise ValueError("incompatible_engine_inputs")


def comparison(root, played, actual_move):
    best = root["variations"][0]
    if best["moves"][0] == actual_move:
        played_variation = best
    elif played is None:
        return {"status":"played_analysis_pending","significant":False,"best":best,"engine_version":root["engine_version"],"root_provenance":root["provenance"]}
    else:
        if not compatible_worker(root, played):
            raise ValueError("incompatible_engine_profile")
        played_variation = next((v for v in played["variations"] if v["moves"][0] == actual_move),None)
        if played_variation is None:
            raise ValueError("played_result_required")
    return {**compare_scores(best,played_variation),"best":best,"played":played_variation,"engine_version":root["engine_version"],"root_provenance":root["provenance"],"played_provenance":(played or root)["provenance"]}
