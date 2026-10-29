"""Current Repertoire mutation binding over #483's sole current snapshot read.

No SQL/model/query duplicate, engine, connection or root app startup. Legacy
canonical repair helpers retain content/history ownership; composition only
selects this factory. The same-owner compatibility imports remain lazy.
"""
from __future__ import annotations

from collections.abc import Callable
import json

from app.contracts.product_v02.analysis import DisplayedCardChange
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import (
    current_snapshot, read_repertoire,
)
from .application import (RepertoireChanges, manual_resolution, resolution_card_updates,
                          validate_proposal, verify_retained_publication)
from .ports import ChangeError, ValidatedChange

SnapshotReader = Callable[[object, str], RepertoireSnapshot]
_shared_snapshot_reader = current_snapshot


def _retained_operations():
    from app import integrity_repair_commands as repairs
    from app.services import canonical_prefix, repertoire_integrity, postgres_integrity
    from app.services.postgres_opening_graph import request_graph_rebuild_in_transaction
    from tempo_legacy_database.adapters.authored_repertoires import integrity_commands
    from tempo_legacy_database.adapters.authored_repertoires.integrity import storage
    from tempo_legacy_database.adapters.opening_graph import worker
    return repairs, canonical_prefix, repertoire_integrity, postgres_integrity, request_graph_rebuild_in_transaction, integrity_commands, storage, worker


def lock_retained_sources(database, repertoire_id: str) -> None:
    """Existing repair lock order; preview enqueues no work."""
    *_, graph_storage = _retained_operations()
    graph_storage.lock_graph_task(database, repertoire_id)
    graph_storage.lock_transition_reservations(database)
    graph_storage.lock_graph_rebuild(database, repertoire_id)


def current_card_definitions(database, repertoire_id: str) -> tuple[DisplayedCardChange, ...]:
    """Current physical definitions from #483's same public read, not FEN joins.

    These are content-update revision facts, not learning/decision mappings.
    They permit explicit approval even when current graph mapping is unavailable.
    """
    facts = read_repertoire(database, repertoire_id)
    colors = {item['trained_color'] for item in facts['lines']}
    if len(colors) != 1: raise ChangeError('repertoire_color_unknown')
    color = next(iter(colors))
    result = []
    for row in facts['cards']:
        if row['archived'] or row['superseded_by'] is not None or row['deleted']:
            continue
        try:
            moves = tuple(json.loads(row['moves_json']))
            if len(moves) > 1000: raise ChangeError('source_exceeds_bound')
            result.append(DisplayedCardChange(action='reuse',presentation_id=row['id'],
                expected_revision=int(row['revision'] or 1),starting_fen=row['start_fen'],
                moves=moves,learner_color=row['trained_color'] or color))
        except (ValueError,TypeError) as error:
            raise ChangeError('invalid_source_card') from error
    if sum(len(item.moves) for item in result) > 100000: raise ChangeError('source_exceeds_bound')
    return tuple(result)


def publish_validated_change(database, change: ValidatedChange, *, current_snapshot: SnapshotReader | None = None) -> RepertoireSnapshot:
    """Atomically publish one approved structural selection in the borrowed tx."""
    from psycopg.pq import TransactionStatus
    if database.raw.info.transaction_status != TransactionStatus.INTRANS:
        raise ChangeError('borrowed_transaction_required')
    reader = current_snapshot if current_snapshot is not None else _shared_snapshot_reader
    repairs, canonical, integrity, certificate, request_graph, storage, integrity_storage, _ = _retained_operations()
    repertoire_id = change.proposal.repertoire_id
    lock_retained_sources(database,repertoire_id)
    if storage.locked_repertoire(database,repertoire_id) is None: raise ChangeError('repertoire_missing')
    current = reader(database,repertoire_id)
    before = change.before.membership
    if (current.membership.revision,current.membership.digest,current.membership.source_revision) != (
            before.revision,before.digest,before.source_revision): raise ChangeError('stale_source')
    definitions = current_card_definitions(database,repertoire_id)
    validated = validate_proposal(current,change.proposal,card_definitions=definitions)
    selections = {(item.position_key,item.proposed_response_uci) for item in change.proposal.edits}
    if change.proposal.engine_evidence or len(selections) != 1:
        raise ChangeError('retained_single_structural_selection_only')
    position,selected = next(iter(selections))
    expected_cards = resolution_card_updates(definitions,position=position,selected_uci=selected)
    if set(item.model_dump_json() for item in expected_cards) != set(item.model_dump_json() for item in change.proposal.card_changes):
        raise ChangeError('displayed_card_change_required')
    before_routes = {route.route_id:route for route in current.membership.routes}
    after_routes = {route.route_id:route for route in validated.after.membership.routes}
    lines, cards = [], []
    for edit in change.proposal.edits:
        original, replacement = before_routes[edit.route_id],after_routes[edit.route_id]
        # #483 supplies actual authored-line IDs. No alternate card-route model.
        source = storage.locked_source_line(database,edit.route_id,repertoire_id)
        if (source is None or source['start_fen'] != original.starting_fen
                or tuple(json.loads(source['moves_json'])) != tuple(original.moves)):
            raise ChangeError('stale_retained_route')
        scoped = canonical.ensure_line_in_scope(database,repertoire_id,replacement.starting_fen,list(replacement.moves),remember=False)
        lines.append((source,list(replacement.moves),scoped))
    for card in expected_cards:
        source = storage.locked_source_card(database,card.presentation_id)
        link = storage.repertoire_card_link(database,repertoire_id,card.presentation_id)
        if (source is None or (source['repertoire_id'] != repertoire_id and link is None)
                or int(source['revision'] or 1) != card.expected_revision):
            raise ChangeError('stale_card_membership')
        original = next(item for item in definitions if item.presentation_id == card.presentation_id)
        if source['start_fen'] != original.starting_fen or tuple(json.loads(source['moves_json'])) != original.moves:
            raise ChangeError('stale_retained_card')
        scoped = canonical.ensure_line_in_scope(database,repertoire_id,card.starting_fen,list(card.moves),remember=False)
        cards.append((source,list(card.moves),scoped))
    from datetime import date
    request_graph(database,repertoire_id,date.today().isoformat())
    admitted = []
    for source,moves,scoped in lines:
        if repairs._replace_repertoire_line(database,source,moves,validated_route=scoped,certify=False): admitted.append(scoped)
    for source,moves,scoped in cards:
        if integrity._rewrite_card(database,repertoire_id,source,moves,validated_route=scoped,certify=False): admitted.append(scoped)
        if source['repertoire_id'] == repertoire_id:
            remaining = integrity_storage.remaining_membership(database,(source['id'],repertoire_id))
            if remaining is not None: integrity_storage.reassign_card_owner(database,(remaining['repertoire_id'],source['id']))
    for scoped in admitted: canonical.certify_admitted_route(database,repertoire_id,scoped)
    certificate.invalidate_integrity_in_transaction(database,repertoire_id)
    published = reader(database,repertoire_id)
    verify_retained_publication(validated,published)
    return published


class RetainedChanges(RepertoireChanges):
    def __init__(self,store,repertoire_id):
        super().__init__(store)
        self.repertoire_id = repertoire_id

    def preview_resolution(self, *, position: str, selected_uci: str, proposal_id: str):
        """Owner preview includes all exact affected physical-card updates."""
        with self.store.transaction(self.repertoire_id) as tx:
            proposal = manual_resolution(tx.snapshot(),repertoire_id=self.repertoire_id,
                position=position,selected_uci=selected_uci,proposal_id=proposal_id,
                card_definitions=tx.card_definitions())
            return self.preview(proposal)


def retained_changes(database, repertoire_id: str, *, current_snapshot: SnapshotReader | None = None) -> RetainedChanges:
    """Concrete factory; all reads use #483 and all mutations use current owners."""
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import PostgresChangeStore
    reader = current_snapshot if current_snapshot is not None else _shared_snapshot_reader
    store = PostgresChangeStore(database.raw,repertoire_id,
        reader=lambda _connection,selected:reader(database,selected),
        publisher=lambda _connection,change:publish_validated_change(database,change,current_snapshot=reader),
        before_lock=lambda _connection,selected:lock_retained_sources(database,selected),
        card_reader=lambda _connection,selected:current_card_definitions(database,selected))
    return RetainedChanges(store,repertoire_id)
