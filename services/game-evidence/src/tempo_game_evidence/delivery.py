"""An HTTP sender for the published v1 protocol, never a Learning writer."""
from datetime import timedelta
import json
import uuid

import httpx
from psycopg.types.json import Jsonb
from tempo_game_evidence_contracts.v2 import EvidenceAcknowledgement, EvidenceDelivery

from .source import utc_now
from .transport import RemoteFailure, request_bytes


def deliver_one(store, endpoint, *, client=None):
    if not endpoint:
        return False
    with store.connect() as database:
        database.execute("""UPDATE outbox current SET status='pending',reason=NULL WHERE current.status='waiting_predecessor'
          AND NOT EXISTS(SELECT 1 FROM outbox previous WHERE previous.game_id=current.game_id
            AND previous.evidence_revision<current.evidence_revision AND previous.attempts>0
            AND previous.status NOT IN ('completed','superseded','terminal_failure','conflict','stale_revision'))""")
        row = database.execute("""SELECT outbox.* FROM outbox JOIN games USING(game_id)
          JOIN workspace_inputs USING(workspace_id)
          JOIN comparisons comparison ON comparison.game_id=outbox.game_id AND comparison.revision=outbox.evidence_revision
          WHERE status IN ('pending','accepted_pending','retryable_failure')
          AND next_attempt_at<=now() AND (leased_until IS NULL OR leased_until<now())
          AND ((outbox.evidence_revision=games.report_revision AND (payload->>'game_revision')::bigint=games.revision
                AND comparison.report->'snapshot'->>'digest'=workspace_inputs.snapshot_digest) OR outbox.attempts>0)
          AND NOT EXISTS(SELECT 1 FROM outbox previous WHERE previous.game_id=outbox.game_id
              AND previous.evidence_revision<outbox.evidence_revision AND previous.attempts>0
              AND previous.status IN ('pending','accepted_pending','retryable_failure'))
          ORDER BY outbox.game_id,evidence_revision LIMIT 1 FOR UPDATE OF outbox SKIP LOCKED""").fetchone()
        if not row:
            return False
        token = uuid.uuid4().hex
        database.execute("UPDATE outbox SET lease_token=%s,leased_until=now()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=%s",
                         (token,row["delivery_id"]))
    payload = EvidenceDelivery.model_validate(row["payload"])
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15, read=10), follow_redirects=False)
    acknowledgement = None
    delay = min(900, 2 ** min(row["attempts"], 10))
    try:
        raw = request_bytes(client, "POST", endpoint, max_bytes=65536, json=payload.model_dump(mode="json"))
        acknowledgement = EvidenceAcknowledgement.model_validate_json(raw)
        if ((acknowledgement.workspace_id, acknowledgement.delivery_id, acknowledgement.evidence_id, acknowledgement.evidence_revision)
                != (payload.workspace_id,payload.delivery_id,payload.evidence_id,payload.evidence_revision)):
            raise RemoteFailure("acknowledgement_identity_mismatch")
        expected = {item.obligation_id for item in payload.obligations}
        accepted = set(acknowledgement.accepted_obligation_ids)
        if (len(acknowledgement.accepted_obligation_ids) != len(accepted) or not accepted <= expected
                or (acknowledgement.status == "completed" and accepted != expected)):
            raise RemoteFailure("acknowledgement_obligation_mismatch")
        status, reason = acknowledgement.status, acknowledgement.reason
    except RemoteFailure as error:
        status = "terminal_failure" if error.terminal else "retryable_failure"
        reason, delay = error.reason, max(delay,error.retry_after)
    except httpx.HTTPError:
        status, reason = "retryable_failure", "learning_unreachable_or_response_lost"
    except (ValueError, json.JSONDecodeError):
        status, reason = "retryable_failure", "invalid_learning_acknowledgement"
    finally:
        if owned:
            client.close()
    with store.connect() as database:
        database.execute("""UPDATE outbox SET status=%s,reason=%s,acknowledgement=%s,
          next_attempt_at=%s,lease_token=NULL,leased_until=NULL WHERE delivery_id=%s AND lease_token=%s""",
          (status,reason,Jsonb(acknowledgement.model_dump(mode="json")) if acknowledgement else None,
           utc_now()+timedelta(seconds=delay),row["delivery_id"],token))
    return True
