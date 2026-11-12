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
  }
};
