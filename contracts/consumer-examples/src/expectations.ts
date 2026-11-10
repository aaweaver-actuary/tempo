/** Independently maintained consumer expectations, never generated from models. */
export type Expectation = { model: string; checks: Record<string, unknown> };
export const expectations: Record<string, Record<string, Expectation>> = {
  "game-evidence": {
    "imported-game":{model:"ImportedGame",checks:{"provenance.provider":"lichess","provenance.provider_game_id":"external-a",played_at:null}},
    delivery:{model:"EvidenceDelivery",checks:{"encounters.0.observed_move":"d2d4","comparisons.0.status":"missed","obligations.0.obligation_id":"obligation-a","obligations.0.source_evidence_id":"evidence-a"}},
    "ack-accepted_pending":{model:"EvidenceAcknowledgement",checks:{status:"accepted_pending",learning_revision:null,"accepted_obligation_ids.0":"obligation-a"}},
    "ack-completed":{model:"EvidenceAcknowledgement",checks:{status:"completed",learning_revision:2,"accepted_obligation_ids.0":"obligation-a"}},
    "ack-retryable_failure":{model:"EvidenceAcknowledgement",checks:{status:"retryable_failure",learning_revision:null}},
    "ack-terminal_failure":{model:"EvidenceAcknowledgement",checks:{status:"terminal_failure",learning_revision:null}},
    "ack-conflict":{model:"EvidenceAcknowledgement",checks:{status:"conflict",learning_revision:null}},
    "ack-stale_revision":{model:"EvidenceAcknowledgement",checks:{status:"stale_revision",learning_revision:null}}
  },
  "analysis-jobs": {
    "stockfish-job":{model:"StockfishJob",checks:{worker:"stockfish",max_depth:12,max_nodes:100000,"constraints.max_wall_time_ms":5000}},
    "maia-job":{model:"MaiaJob",checks:{worker:"maia",rating:1500,model_id:"maia-2","legal_moves.length":2}},
    "stockfish-result":{model:"StockfishResult",checks:{engine_version:"18","variations.0.score.kind":"centipawns","variations.0.score.value":30,"variations.0.score_perspective":"side_to_move"}},
    "maia-result":{model:"MaiaResult",checks:{model_version:"fixture-1","predictions.0.probability":0.6,"predictions.1.probability":0.4}},
    "status-pending":{model:"JobStatus",checks:{status:"pending",retryable:false}},
    "status-running":{model:"JobStatus",checks:{status:"running",retryable:false}},
    "status-completed":{model:"JobStatus",checks:{status:"completed",retryable:false}},
    "status-failed":{model:"JobStatus",checks:{status:"failed",retryable:true,reason:"execution_interrupted"}},
    "status-cancelled":{model:"JobStatus",checks:{status:"cancelled",retryable:false}},
    "cancel-request":{model:"CancellationRequest",checks:{cancellation_id:"cancel-a",execution_generation:1}},
    "cancel-requested":{model:"CancellationAcknowledgement",checks:{status:"requested",execution_generation:1}},
    "cancel-terminated":{model:"CancellationAcknowledgement",checks:{status:"terminated",execution_generation:1}},
    "cancel-already_completed":{model:"CancellationAcknowledgement",checks:{status:"already_completed"}},
    "cancel-stale_generation":{model:"CancellationAcknowledgement",checks:{status:"stale_generation"}}
  }
};
