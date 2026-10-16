"""Independent installed-contract/chess consumer; imports no worker runtime."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import chess
from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import stockfish_input_digest


def consume(exchange):
    jobs = {}
    accepted = {}
    counts = {"results": 0, "statuses": 0, "acknowledgements": 0}
    for record in exchange["records"]:
        model = getattr(v1, record["model"])
        payload = model.model_validate(record["payload"])
        key = (payload.workspace_id, payload.job_id, payload.request_revision, payload.execution_generation)
        if isinstance(payload, v1.StockfishJob):
            assert stockfish_input_digest(payload) == payload.input_digest
            if key in jobs:
                assert jobs[key] == payload
            jobs[key] = payload
        elif isinstance(payload, v1.StockfishResult):
            job = jobs[key]
            assert payload.engine_version == job.engine_version
            assert payload.provenance.input_digest == job.input_digest
            board = chess.Board(job.fen)
            assert board.is_valid()
            for text in job.moves:
                move = chess.Move.from_uci(text)
                assert move in board.legal_moves
                board.push(move)
            roots = set(job.search_moves) if job.search_moves else {move.uci() for move in board.legal_moves}
            assert len(payload.variations) == min(job.multi_pv, len(roots))
            assert len({variation.moves[0] for variation in payload.variations}) == len(payload.variations)
            assert len({variation.depth for variation in payload.variations}) == 1
            for variation in payload.variations:
                assert variation.moves[0] in roots
                assert variation.score_perspective == "side_to_move"
                assert job.max_depth is None or variation.depth <= job.max_depth
                position = board.copy(stack=True)
                for text in variation.moves:
                    move = chess.Move.from_uci(text)
                    assert move in position.legal_moves
                    position.push(move)
            canonical = v1.serialize_contract(payload)
            if key in accepted:
                assert accepted[key] == canonical
            accepted[key] = canonical
            counts["results"] += 1
        elif isinstance(payload, v1.JobStatus):
            counts["statuses"] += 1
        elif isinstance(payload, v1.CancellationAcknowledgement):
            counts["acknowledgements"] += 1
    assert counts["results"] > 0
    return {"success": True, **counts, "distinct_accepted_executions": len(accepted)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", required=True)
    arguments = parser.parse_args()
    print(json.dumps(consume(json.loads(Path(arguments.exchange).read_text()))))
