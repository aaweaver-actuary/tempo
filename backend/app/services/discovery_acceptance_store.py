"""PostgreSQL Discovery acceptance adapter; never opens or completes a transaction."""

import json
from typing import TYPE_CHECKING

from .canonical_prefix import read_prefix
from .canonical_scope_freshness import game_scope_generation
from .discovery_acceptance_contracts import (
    DiscoveryChoice, DiscoveryOpportunity, DiscoveryScope, LockedDiscoveryOpportunity,
    MatchingDiscoveryIntent, NewDiscoveryIntent,
)
from .discovery_admission import DISCOVERY_ADMISSION_PRIORITY
from .durable_tasks import enqueue_compact_postgres_task_in_transaction

if TYPE_CHECKING:
    from ..postgres_store import PostgresConnection


class PostgresDiscoveryAcceptanceStore:
    def __init__(self, database: "PostgresConnection"):
        self._database = database

    def has_matching_intent(self, choice: DiscoveryChoice) -> bool:
        return self._matching_intent(choice, lock=False) is not None

    def lock_matching_intent(self, choice: DiscoveryChoice) -> MatchingDiscoveryIntent | None:
        return self._matching_intent(choice, lock=True)

    def _matching_intent(self, choice: DiscoveryChoice, *, lock: bool) -> MatchingDiscoveryIntent | None:
        row = self._database.execute_native(
            "SELECT id,state FROM discovery_admission_intents "
            "WHERE opportunity_id=%s AND selected_move_uci=%s AND evidence_fingerprint=%s"
            + (" FOR UPDATE" if lock else ""),
            (choice.opportunity_id, choice.selected_move_uci, choice.evidence_fingerprint),
        ).fetchone()
        return MatchingDiscoveryIntent(row["id"], row["state"]) if row is not None else None

    def lock_opportunity_scope(self, opportunity_id: str) -> LockedDiscoveryOpportunity | None:
        owner = self._database.execute_native(
            "SELECT repertoire_id FROM repertoire_opportunities WHERE id=%s", (opportunity_id,),
        ).fetchone()
        if owner is not None:
            # Preserve the existing scope-before-opportunity lock order.
            read_prefix(self._database, owner[0], lock=True)
            game_scope_generation(self._database, lock=True)
        row = self._database.execute_native(
            "SELECT * FROM repertoire_opportunities WHERE id=%s FOR UPDATE", (opportunity_id,),
        ).fetchone()
        if row is None:
            return None
        opportunity = dict(row)
        # Read the authoritative facts after the opportunity lock, as before.
        prefix = read_prefix(self._database, opportunity["repertoire_id"])
        current = DiscoveryScope(prefix["revision"], prefix["source_revision"],
                                 prefix["preview_id"], game_scope_generation(self._database))
        observed = DiscoveryScope(
            opportunity.get("canonical_prefix_revision", 0),
            opportunity.get("canonical_scope_source_revision"),
            opportunity.get("canonical_scope_preview_id"),
            opportunity.get("game_scope_generation", 0),
        )
        return LockedDiscoveryOpportunity(DiscoveryOpportunity(
            opportunity["repertoire_id"], opportunity["status"], opportunity["card_id"],
            opportunity["evidence_fingerprint"], observed,
        ), current, bool(prefix["moves"]))

    def task_state(self, intent_id: str) -> str | None:
        row = self._database.execute_native(
            "SELECT state FROM background_tasks WHERE kind='discovery_admission' "
            "AND deduplication_key=%s", (intent_id,),
        ).fetchone()
        return row["state"] if row is not None else None

    def requeue_intent(self, intent_id: str) -> None:
        enqueue_compact_postgres_task_in_transaction(
            self._database, "discovery_admission", intent_id, {"intent_id": intent_id},
            priority=DISCOVERY_ADMISSION_PRIORITY,
        )

    def persist_intent_and_task(self, intent: NewDiscoveryIntent) -> None:
        self._database.execute_native(
            "INSERT INTO discovery_admission_intents("
            "id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,"
            "selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (intent.id, intent.choice.opportunity_id, intent.repertoire_id,
             intent.choice.evidence_fingerprint, intent.starting_fen, intent.choice.selected_move_uci,
             json.dumps(intent.preview_moves_uci), json.dumps(intent.recommendation), intent.line_id,
             intent.accepted_at, intent.accepted_at),
        )
        self._database.execute_native(
            "UPDATE repertoire_opportunities SET admission_state='preparing',"
            "seen_at=COALESCE(seen_at,%s),updated_at=%s WHERE id=%s",
            (intent.accepted_at, intent.accepted_at, intent.choice.opportunity_id),
        )
        self.requeue_intent(intent.id)
