"""Learning-only PostgreSQL persistence borrowing an explicit outer transaction."""
from dataclasses import asdict
import json

from tempo_game_evidence_contracts import v1, v2

from .models import (AcceptedDecision, AdmissionResult, AttemptAuthority, ReceiveContext,
                     RemediationError, ReviewOutcome, WorkItem, WorkPage)
from .policy import PAGE_SIZE
from .validation import canonical, digest, instant, logical_hash, validate_delivery


_OUTSTANDING = """
 FROM remediation_obligation_heads head
 JOIN remediation_contributions contribution USING(workspace_id,obligation_id,obligation_revision)
 JOIN remediation_incident_versions incident ON incident.workspace_id=contribution.workspace_id
   AND incident.incident_id=contribution.incident_id AND incident.logical_hash=contribution.logical_hash
 WHERE head.workspace_id=%s AND head.card_id=%s AND head.active
   AND contribution.kind='real_game_miss'
   AND NOT EXISTS (
     SELECT 1 FROM remediation_settlements settlement
     JOIN remediation_attempts attempt USING(workspace_id,attempt_id)
     WHERE settlement.workspace_id=head.workspace_id AND settlement.card_id=head.card_id
       AND NOT settlement.invalidated
       AND incident.work_sequence<=attempt.frontier_sequence
       AND incident.accepted_at<=attempt.issued_at
       AND contribution.card_revision=attempt.card_revision
       AND EXISTS(SELECT 1 FROM remediation_game_heads game
           WHERE game.workspace_id=head.workspace_id AND game.game_id=contribution.game_id
             AND game.game_revision=contribution.game_revision)
       AND settlement.reviewed_at>=attempt.issued_at
       AND (contribution.occurred_at IS NULL OR contribution.occurred_at<=settlement.reviewed_at))
"""


class PostgresRepository:
    """Each mutating call holds one short workspace-local module lock.

    The caller owns connection/transaction lifetime and foreground admission.
    No lock is retained across preparation pages or external work. B's lock must
    follow A's card/issuance locks; module callbacks must not acquire A's locks.
    """
    def __init__(self, connection, workspace_id: str, *, background: bool = False):
        self.connection = connection
        self.workspace_id = workspace_id
        self.background = background
        self._transaction()

    def _transaction(self):
        if self.connection.info.transaction_status.name != "INTRANS":
            raise RuntimeError("remediation requires an explicit active outer transaction")

    def _execute(self, statement, parameters=()):
        self._transaction()
        return self.connection.execute(statement, parameters)

    def lock(self):
        if self.background:
            if not self._execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                                 ("tempo:learning-remediation:" + self.workspace_id,)).fetchone()[0]:
                raise RemediationError("retryable_failure", "foreground_busy")
            return
        self._execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                      ("tempo:learning-remediation:" + self.workspace_id,))

    def _ack(self, delivery, status, *, reason=None, revision=None):
        module = v2 if isinstance(delivery, v2.EvidenceDelivery) else v1
        return module.EvidenceAcknowledgement(contract_version=delivery.contract_version,
            workspace_id=self.workspace_id, delivery_id=delivery.delivery_id,
            evidence_id=delivery.evidence_id, evidence_revision=delivery.evidence_revision,
            status=status, reason=reason, learning_revision=revision,
            accepted_obligation_ids=([item.obligation_id for item in delivery.obligations]
                                     if status == "completed" else []))

    def receipt(self, delivery_id: str):
        row = self._execute("SELECT response_json FROM remediation_deliveries WHERE workspace_id=%s AND delivery_id=%s",
                            (self.workspace_id, delivery_id)).fetchone()
        if row is None:
            return None
        module = v2 if row[0]["contract_version"] == 2 else v1
        return module.EvidenceAcknowledgement.model_validate(row[0])

    def _existing_delivery(self, delivery):
        if delivery.workspace_id != self.workspace_id:
            raise RemediationError("terminal_failure", "workspace_authority_mismatch")
        row = self._execute("SELECT payload_hash,response_json FROM remediation_deliveries WHERE workspace_id=%s AND delivery_id=%s",
                            (self.workspace_id, delivery.delivery_id)).fetchone()
        if row and row[0] != digest(delivery.model_dump(mode="json")):
            raise RemediationError("conflict", "delivery_id_reused")
        return row[1] if row else None

    def _save_receipt(self, delivery, response):
        self._execute("""INSERT INTO remediation_deliveries VALUES(%s,%s,%s,%s::jsonb)
            ON CONFLICT(workspace_id,delivery_id) DO UPDATE SET response_json=excluded.response_json""",
            (self.workspace_id, delivery.delivery_id, digest(delivery.model_dump(mode="json")), response.model_dump_json()))

    def record_status(self, delivery, status: str, reason: str):
        """Caller may persist pending/failure in its own healthy transaction."""
        if status not in {"accepted_pending", "retryable_failure", "terminal_failure"}:
            raise ValueError("record_status is only for pending/failure")
        self.lock()
        existing = self._existing_delivery(delivery)
        if existing and existing["status"] == "completed":
            return self.receipt(delivery.delivery_id)
        response = self._ack(delivery, status, reason=reason)
        self._save_receipt(delivery, response)
        return response

    def receive(self, delivery, context: ReceiveContext):
        """Validate completely before writes; acceptance and receipt share the caller's commit."""
        try:
            self.lock()
            if (context.workspace_id, context.game_id, context.evidence_id) != (
                    self.workspace_id, delivery.game_id, delivery.evidence_id):
                raise RemediationError("terminal_failure", "source_authority_mismatch")
            existing = self._existing_delivery(delivery)
            if existing and existing["status"] == "completed":
                return self.receipt(delivery.delivery_id)
            payload_hash = digest(delivery.model_dump(mode="json", exclude={"delivery_id"}))
            evidence = self._execute("""SELECT payload_hash,learning_revision FROM remediation_evidence
                WHERE workspace_id=%s AND evidence_id=%s AND evidence_revision=%s""",
                (self.workspace_id, delivery.evidence_id, delivery.evidence_revision)).fetchone()
            if evidence:
                if evidence[0] != payload_hash:
                    raise RemediationError("conflict", "evidence_revision_reused")
                response = self._ack(delivery, "completed", revision=evidence[1])
                self._save_receipt(delivery, response)
                return response
            contributions = validate_delivery(delivery, context)
            self._check_sources(delivery)
            self._check_contributions(delivery, contributions)
        except RemediationError as error:
            return self._ack(delivery, error.status, reason=error.reason)
        self._execute("INSERT INTO remediation_workspaces(workspace_id) VALUES(%s) ON CONFLICT DO NOTHING", (self.workspace_id,))
        revision = self._execute("UPDATE remediation_workspaces SET revision=revision+1 WHERE workspace_id=%s RETURNING revision",
                                 (self.workspace_id,)).fetchone()[0]
        for contribution in contributions:
            self._insert_contribution(delivery, contribution, instant(context.received_at))
        for withdrawal in getattr(delivery, "withdrawals", ()):
            self._execute("""UPDATE remediation_obligation_heads SET active=false
                WHERE workspace_id=%s AND obligation_id=%s AND obligation_revision=%s""",
                (self.workspace_id, withdrawal.obligation_id, withdrawal.expected_obligation_revision))
        self._execute("INSERT INTO remediation_evidence VALUES(%s,%s,%s,%s,%s,%s,%s)",
                      (self.workspace_id, delivery.evidence_id, delivery.evidence_revision,
                       delivery.game_id, delivery.game_revision, payload_hash, revision))
        self._execute("""INSERT INTO remediation_source_heads VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(workspace_id,evidence_id) DO UPDATE SET
            evidence_revision=excluded.evidence_revision,game_revision=excluded.game_revision""",
            (self.workspace_id, delivery.evidence_id, delivery.game_id, delivery.evidence_revision, delivery.game_revision))
        self._execute("""INSERT INTO remediation_game_heads VALUES(%s,%s,%s)
            ON CONFLICT(workspace_id,game_id) DO UPDATE SET game_revision=excluded.game_revision""",
            (self.workspace_id, delivery.game_id, delivery.game_revision))
        response = self._ack(delivery, "completed", revision=revision)
        self._save_receipt(delivery, response)
        return response

    def _check_sources(self, delivery):
        source = self._execute("""SELECT game_id,evidence_revision,game_revision FROM remediation_source_heads
            WHERE workspace_id=%s AND evidence_id=%s""", (self.workspace_id, delivery.evidence_id)).fetchone()
        if source:
            if source[0] != delivery.game_id:
                raise RemediationError("conflict", "evidence_game_reused")
            if delivery.evidence_revision <= source[1] or delivery.game_revision < source[2]:
                raise RemediationError("stale_revision", "source_revision_superseded")
        game = self._execute("SELECT game_revision FROM remediation_game_heads WHERE workspace_id=%s AND game_id=%s",
                             (self.workspace_id, delivery.game_id)).fetchone()
        if game and delivery.game_revision < game[0]:
            raise RemediationError("stale_revision", "game_revision_superseded")

    def _check_contributions(self, delivery, contributions):
        for contribution in contributions:
            existing = self._execute("""SELECT payload_json FROM remediation_contributions
                WHERE workspace_id=%s AND obligation_id=%s AND obligation_revision=%s""",
                (self.workspace_id, contribution.obligation_id, contribution.obligation_revision)).fetchone()
            if existing and existing[0] != contribution.payload_json:
                raise RemediationError("conflict", "obligation_revision_reused")
            head = self._execute("""SELECT evidence_id,game_id,incident_id,obligation_revision,active
                FROM remediation_obligation_heads WHERE workspace_id=%s AND obligation_id=%s""",
                (self.workspace_id, contribution.obligation_id)).fetchone()
            if head:
                if head[:3] != (delivery.evidence_id, delivery.game_id, contribution.incident_id):
                    raise RemediationError("conflict", "obligation_lineage_reused")
                if contribution.obligation_revision < head[3] or (contribution.obligation_revision == head[3] and not head[4]):
                    raise RemediationError("stale_revision", "obligation_revision_superseded")
        for withdrawal in getattr(delivery, "withdrawals", ()):
            head = self._execute("""SELECT evidence_id,game_id,obligation_revision FROM remediation_obligation_heads
                WHERE workspace_id=%s AND obligation_id=%s""", (self.workspace_id, withdrawal.obligation_id)).fetchone()
            if head is None or head[:2] != (delivery.evidence_id, delivery.game_id):
                raise RemediationError("conflict", "withdrawal_source_mismatch")
            if head[2] != withdrawal.expected_obligation_revision:
                raise RemediationError("stale_revision", "withdrawal_revision_superseded")

    def _insert_contribution(self, delivery, contribution, received_at):
        self._execute("INSERT INTO remediation_cards(workspace_id,card_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                      (self.workspace_id, contribution.card_id))
        fact_hash = logical_hash(contribution)
        known = self._execute("""SELECT work_sequence FROM remediation_incident_versions
            WHERE workspace_id=%s AND incident_id=%s AND logical_hash=%s""",
            (self.workspace_id, contribution.incident_id, fact_hash)).fetchone()
        if not known:
            sequence = self._execute("""UPDATE remediation_cards SET sequence=sequence+1,work_state='pending',reason=NULL,queue_entry_id=NULL
                WHERE workspace_id=%s AND card_id=%s RETURNING sequence""", (self.workspace_id, contribution.card_id)).fetchone()[0]
            self._execute("INSERT INTO remediation_incident_versions VALUES(%s,%s,%s,%s,%s,%s)",
                          (self.workspace_id, contribution.incident_id, fact_hash, contribution.card_id, sequence, received_at))
        self._execute("""INSERT INTO remediation_contributions VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(workspace_id,obligation_id,obligation_revision) DO NOTHING""",
            (self.workspace_id, contribution.obligation_id, contribution.obligation_revision, delivery.evidence_id,
             delivery.evidence_revision, delivery.game_id, contribution.incident_id, fact_hash, contribution.card_id,
             contribution.card_revision, contribution.publication_id, contribution.publication_generation,
             contribution.kind, contribution.occurred_at, contribution.payload_json, delivery.game_revision))
        self._execute("""INSERT INTO remediation_obligation_heads VALUES(%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT(workspace_id,obligation_id) DO UPDATE SET obligation_revision=excluded.obligation_revision,active=true""",
            (self.workspace_id, contribution.obligation_id, contribution.obligation_revision, delivery.evidence_id,
             delivery.game_id, contribution.incident_id, contribution.card_id))

    def has_outstanding(self, card_id: str) -> bool:
        return self._execute("SELECT EXISTS(SELECT 1" + _OUTSTANDING + ")", (self.workspace_id, card_id)).fetchone()[0]

    def has_current_miss(self, candidate) -> bool:
        """Retained stale obligations are visible but cannot grant admission."""
        return self._execute("SELECT EXISTS(SELECT 1" + _OUTSTANDING + """
            AND contribution.card_revision=%s AND contribution.publication_id=%s
            AND contribution.publication_generation=%s
            AND EXISTS(SELECT 1 FROM remediation_game_heads game
                WHERE game.workspace_id=head.workspace_id AND game.game_id=contribution.game_id
                  AND game.game_revision=contribution.game_revision))""",
            (self.workspace_id, candidate.card_id, candidate.card_revision, candidate.publication_id,
             candidate.publication_generation)).fetchone()[0]

    def pending_page(self, cursor: str = "") -> WorkPage:
        rows = self._execute("""SELECT card_id,sequence,work_state,reason FROM remediation_cards
            WHERE workspace_id=%s AND card_id>%s ORDER BY card_id LIMIT %s""",
            (self.workspace_id, cursor, PAGE_SIZE + 1)).fetchall()
        scanned = rows[:PAGE_SIZE]
        return WorkPage(tuple(WorkItem(*row) for row in scanned if self.has_outstanding(row[0])),
                        scanned[-1][0] if scanned else cursor, len(rows) > PAGE_SIZE)

    def record_admission(self, card_id: str, result: AdmissionResult):
        if result.status not in {"applied", "deferred", "rejected"}:
            raise ValueError("unknown admission result")
        if result.status != "applied" and not result.reason:
            raise ValueError("non-applied admission requires its actual reason")
        self._execute("""UPDATE remediation_cards SET work_state=%s,reason=%s,queue_entry_id=%s
            WHERE workspace_id=%s AND card_id=%s""",
            ("admitted" if result.status == "applied" else "deferred", result.reason, result.queue_entry_id, self.workspace_id, card_id))

    def _check_attempt(self, attempt: AttemptAuthority, current: AcceptedDecision):
        instant(attempt.issued_at)
        if current.workspace_id != self.workspace_id:
            raise RemediationError("terminal_failure", "accepted_workspace_mismatch")
        if not current.active or (attempt.card_id, attempt.card_revision, attempt.publication_id, attempt.publication_generation) != (
                current.card_id, current.card_revision, current.publication_id, current.publication_generation):
            raise RemediationError("stale_revision", "attempt_authority_changed")

    def bind_attempt(self, attempt: AttemptAuthority, current: AcceptedDecision) -> int:
        self.lock()
        self._check_attempt(attempt, current)
        identity = (attempt.card_id, attempt.card_revision, attempt.publication_id,
                    attempt.publication_generation, instant(attempt.issued_at))
        existing = self._execute("""SELECT card_id,card_revision,publication_id,publication_generation,issued_at,frontier_sequence
            FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""", (self.workspace_id, attempt.attempt_id)).fetchone()
        if existing:
            if existing[:5] != identity:
                raise RemediationError("conflict", "attempt_id_reused")
            return existing[5]
        card = self._execute("SELECT sequence FROM remediation_cards WHERE workspace_id=%s AND card_id=%s",
                             (self.workspace_id, attempt.card_id)).fetchone()
        frontier = card[0] if card else 0
        self._execute("INSERT INTO remediation_attempts VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                      (self.workspace_id, attempt.attempt_id, *identity, frontier))
        return frontier

    def settle(self, review: ReviewOutcome, current: AcceptedDecision) -> bool:
        self.lock()
        self._check_attempt(review.attempt, current)
        payload = asdict(review)
        payload["reviewed_at"] = instant(review.reviewed_at).isoformat()
        payload["attempt"]["issued_at"] = instant(review.attempt.issued_at).isoformat()
        payload_json = canonical(payload)
        prior = self._execute("SELECT payload_json,settled FROM remediation_review_receipts WHERE workspace_id=%s AND review_id=%s",
                              (self.workspace_id, review.review_id)).fetchone()
        if prior:
            if prior[0] != payload_json:
                raise RemediationError("conflict", "review_id_reused")
            return prior[1]
        attempt = self._execute("""SELECT card_id,card_revision,publication_id,publication_generation,issued_at,frontier_sequence
            FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""", (self.workspace_id, review.attempt.attempt_id)).fetchone()
        expected = (review.attempt.card_id, review.attempt.card_revision, review.attempt.publication_id,
                    review.attempt.publication_generation, instant(review.attempt.issued_at))
        if attempt is None or attempt[:5] != expected:
            raise RemediationError("conflict", "attempt_frontier_missing_or_changed")
        qualifies = (review.source_kind == "study" and review.outcome == "correct" and
                     not (review.guided or review.failed or review.invalidated) and
                     instant(review.reviewed_at) >= instant(review.attempt.issued_at))
        settled = False
        if qualifies:
            settled = self._execute("SELECT EXISTS(SELECT 1" + _OUTSTANDING + """
                AND incident.work_sequence<=%s AND incident.accepted_at<=%s
                AND contribution.card_revision=%s AND contribution.publication_id=%s
                AND contribution.publication_generation=%s
                AND EXISTS(SELECT 1 FROM remediation_game_heads game
                    WHERE game.workspace_id=head.workspace_id AND game.game_id=contribution.game_id
                      AND game.game_revision=contribution.game_revision)
                AND (contribution.occurred_at IS NULL OR contribution.occurred_at<=%s))""",
                (self.workspace_id, review.attempt.card_id, attempt[5], instant(review.attempt.issued_at),
                 review.attempt.card_revision, review.attempt.publication_id, review.attempt.publication_generation,
                 instant(review.reviewed_at))).fetchone()[0]
            self._execute("INSERT INTO remediation_settlements(workspace_id,review_id,attempt_id,card_id,reviewed_at) VALUES(%s,%s,%s,%s,%s)",
                          (self.workspace_id, review.review_id, review.attempt.attempt_id, review.attempt.card_id, instant(review.reviewed_at)))
        self._execute("INSERT INTO remediation_review_receipts VALUES(%s,%s,%s,%s)",
                      (self.workspace_id, review.review_id, payload_json, settled))
        return settled

    def invalidate_settlement(self, review_id: str, reason: str):
        self.lock()
        if not reason:
            raise ValueError("invalidation requires a reason")
        self._execute("UPDATE remediation_settlements SET invalidated=true,invalidation_reason=%s WHERE workspace_id=%s AND review_id=%s",
                      (reason, self.workspace_id, review_id))
