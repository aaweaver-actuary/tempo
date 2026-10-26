"""Current Repertoire owner binding; no SQL, engine, connection or root app startup.

This adapter reuses existing Repertoire repair/certification/invalidation writers.
The default retained audit read uses shared snapshot values and #483 public index.
Composition selects this factory; it does not implement any content mutation.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json

from app.contracts.product_v02.analysis import DisplayedCardChange
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot
from app.repertoire import build_index
from tempo_game_evidence_contracts.legacy import decision_identity
from .application import RepertoireChanges, validate_proposal, verify_retained_publication
from .ports import ChangeError, ValidatedChange

SnapshotReader = Callable[[object, str], RepertoireSnapshot]



def _retained_operations():
    # These are existing logical Repertoire owners despite their legacy root
    # paths. Import lazily only at the infrastructure binding; pure policy has
    # no command-registration, concrete storage or compatibility dependency.
    from app import integrity_repair_commands as repairs
    from app.services import canonical_prefix, repertoire_integrity, postgres_integrity
    from app.services.postgres_opening_graph import request_graph_rebuild_in_transaction
    from tempo_legacy_database.adapters.authored_repertoires import integrity_commands
    from tempo_legacy_database.adapters.authored_repertoires.integrity import storage
    from tempo_legacy_database.adapters.opening_graph import worker
    return repairs, canonical_prefix, repertoire_integrity, postgres_integrity, request_graph_rebuild_in_transaction, integrity_commands, storage, worker


def lock_retained_sources(database, repertoire_id: str) -> None:
    """The existing repair lock order, without enqueueing work during preview."""
    *_, graph_storage = _retained_operations()
    graph_storage.lock_graph_task(database, repertoire_id)
    graph_storage.lock_transition_reservations(database)
    graph_storage.lock_graph_rebuild(database, repertoire_id)


def _source_kind(route_id: str) -> tuple[str, str]:
    if route_id.startswith("line:") or route_id.startswith("card:"):
        return tuple(route_id.split(":", 1))
    # Existing public authored snapshots use bare authored-line IDs. Physical
    # card-source routes must be explicitly namespaced; never guess by FEN.
    return "line", route_id


def publish_validated_change(database, change: ValidatedChange, *, current_snapshot: SnapshotReader | None = None) -> RepertoireSnapshot:
    """Publish one explicitly approved structural selection in the caller tx.

    C intentionally has no engine proposals/import activation or independent new
    card additions. An edited existing card definition needs a displayed update
    with its actual identity/revision; the existing canonical writer may assign
    a replacement physical ID while retaining the original card and history.
    """
    from psycopg.pq import TransactionStatus
    if database.raw.info.transaction_status != TransactionStatus.INTRANS:
        raise ChangeError("borrowed_transaction_required")
    reader = current_snapshot if current_snapshot is not None else _default_snapshot_reader
    repairs, canonical, integrity, certificate, request_graph, storage, integrity_storage, _ = _retained_operations()
    repertoire_id = change.proposal.repertoire_id
    lock_retained_sources(database, repertoire_id)
    if storage.locked_repertoire(database, repertoire_id) is None:
        raise ChangeError("repertoire_missing")
    current = reader(database, repertoire_id)
    before = change.before.membership
    if (current.membership.revision, current.membership.digest, current.membership.source_revision) != (
            before.revision, before.digest, before.source_revision):
        raise ChangeError("stale_source")
    validated = validate_proposal(current, change.proposal)
    if change.proposal.engine_evidence:
        raise ChangeError("retained_structural_selection_only")
    expected_after = {route.route_id: route for route in validated.after.membership.routes}
    before_routes = {route.route_id: route for route in current.membership.routes}
    line_changes, card_changes = [], []
    consumed_cards = set()
    for edit in change.proposal.edits:
        original = before_routes[edit.route_id]
        replacement = expected_after[edit.route_id]
        kind, source_id = _source_kind(edit.route_id)
        source = (storage.locked_source_line(database, source_id, repertoire_id)
                  if kind == "line" else storage.locked_source_card(database, source_id))
        if (source is None or source["start_fen"] != original.starting_fen
                or tuple(json.loads(source["moves_json"])) != tuple(original.moves)):
            raise ChangeError("stale_retained_route")
        if kind == "card":
            link = storage.repertoire_card_link(database, repertoire_id, source_id)
            if source["repertoire_id"] != repertoire_id and link is None:
                raise ChangeError("stale_card_membership")
            expected = DisplayedCardChange(action="update", presentation_id=source_id,
                expected_revision=int(source["revision"] or 1), starting_fen=original.starting_fen,
                moves=tuple(replacement.moves), learner_color=original.learner_color)
            matches = [item for item in change.proposal.card_changes if item == expected]
            if len(matches) != 1:
                raise ChangeError("displayed_card_change_required")
            consumed_cards.add(matches[0].model_dump_json())
        scoped = canonical.ensure_line_in_scope(database, repertoire_id, replacement.starting_fen,
                                                list(replacement.moves), remember=False)
        target = (line_changes if kind == "line" else card_changes)
        target.append((source, list(replacement.moves), scoped))
    if any(item.model_dump_json() not in consumed_cards for item in change.proposal.card_changes):
        raise ChangeError("retained_card_change_not_supported")
    # Queue only the existing current-content graph refresh. This never executes
    # the held migration, builds a graph or starts a worker/service.
    from datetime import date
    request_graph(database, repertoire_id, date.today().isoformat())
    admitted = []
    for source, moves, scoped in line_changes:
        if repairs._replace_repertoire_line(database, source, moves, validated_route=scoped, certify=False):
            admitted.append(scoped)
    for source, moves, scoped in card_changes:
        # A new physical definition is covered by the exact displayed update;
        # the old card/history and all unrelated memberships remain retained.
        if integrity._rewrite_card(database, repertoire_id, source, moves, validated_route=scoped, certify=False):
            admitted.append(scoped)
        if source["repertoire_id"] == repertoire_id:
            remaining = integrity_storage.remaining_membership(database, (source["id"], repertoire_id))
            if remaining is not None:
                integrity_storage.reassign_card_owner(database, (remaining["repertoire_id"], source["id"]))
    for scoped in admitted:
        canonical.certify_admitted_route(database, repertoire_id, scoped)
    certificate.invalidate_integrity_in_transaction(database, repertoire_id)
    published = reader(database, repertoire_id)
    verify_retained_publication(validated, published)
    return published


def retained_changes(database, repertoire_id: str, *, current_snapshot: SnapshotReader | None = None) -> RepertoireChanges:
    """Actual retained owner factory; a compatible shared reader may be supplied.

    Borrow the original PostgresConnection, including its TempoRow/index access.
    Source/publication/receipt all use its one raw transaction. No connection,
    schema installation or domain writer is created in composition.
    """
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import PostgresChangeStore
    reader = current_snapshot if current_snapshot is not None else _default_snapshot_reader
    store = PostgresChangeStore(database.raw, repertoire_id,
        reader=lambda _connection, selected: reader(database, selected),
        publisher=lambda _connection, change: publish_validated_change(database, change, current_snapshot=reader),
        before_lock=lambda _connection, selected: lock_retained_sources(database, selected))
    return RepertoireChanges(store)


def current_snapshot(database, repertoire_id: str) -> RepertoireSnapshot:
    """Retained-owner audit/approval read; no game or clean-integrity prerequisite.

    All source routes are explicit current authored lines/card definitions. A
    card-source route carries its actual stored physical identity; authored-line
    physical mapping stays unknown. This conservative reader is sufficient for
    conflict approval and does not manufacture game-lapse availability authority.
    """
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import current_source_facts
    facts = current_source_facts(database, repertoire_id)
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > 32 * 1024 * 1024:
        raise ChangeError("source_exceeds_bound")
    source_revision = int(facts["scope_source_revision"])
    if source_revision < 0:
        raise ChangeError("invalid_source_revision")
    revision = source_revision + 1  # Existing counter starts at zero; shared revisions are positive.
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    routes, boundaries = [], []
    colors = set()
    for source in facts["sources"]:
        try:
            moves = json.loads(source["moves_json"])
            if not isinstance(moves, list) or not all(isinstance(item,str) for item in moves):
                raise ValueError("invalid move payload")
            if source["trained_color"] not in {"white","black"}:
                raise ValueError("unknown learner color")
            colors.add(source["trained_color"])
            routes.append(dict(route_id=source["source_id"], repertoire_id=repertoire_id,
                policy_id=repertoire_id, learner_color=source["trained_color"],
                starting_fen=source["start_fen"], moves=moves, reentries=[]))
            boundaries.append(dict(route_id=source["source_id"],terminal_ply=len(moves),deliberate=True))
        except (ValueError,KeyError,TypeError) as error:
            raise ChangeError("invalid_source:" + str(source.get("source_id","unknown"))) from error
    if len(colors) != 1:
        raise ChangeError("learner_color_unavailable" if not colors else "mixed_learner_colors")
    available = True
    payload = dict(membership=dict(workspace_id="retained-repertoire",snapshot_id="retained:"+digest,
        revision=revision,digest=digest,source_revision=f"retained:{source_revision}",
        captured_at=datetime.now(timezone.utc).isoformat(),scope_id=repertoire_id,
        scope_repertoire_ids=[repertoire_id],complete=available,available=available,routes=routes),
        variant="standard",learner_color=next(iter(colors)),
        boundaries=boundaries,decision_presentations=[])
    try:
        snapshot = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    except ValueError as error:
        raise ChangeError("invalid_source") from error
    index = build_index(snapshot)
    mappings = []
    for source in facts["sources"]:
        physical_id = source["presentation_id"]
        if physical_id is None:
            continue
        # An invalid card tail cannot establish a current mapping from its prefix.
        if any(item.route_id == source["source_id"] for item in index.diagnostics
               if item.code != "own_response_conflict"):
            continue
        introduced_at, introduction = None, "unknown"
        timestamp = source["introduced_at"]
        if timestamp is None:
            introduction = "unintroduced"
        else:
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z","+00:00"))
                if "T" in timestamp and parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed):
                    introduced_at, introduction = timestamp, "introduced"
            except (ValueError,TypeError,AttributeError):
                pass
        for occurrence in index.routes[repertoire_id,source["source_id"]]:
            if not occurrence.learner_turn or occurrence.next_move is None:
                continue
            mappings.append(DecisionPresentation(decision_id=decision_identity(physical_id,occurrence.ply,occurrence.history),
                repertoire_id=repertoire_id,position_key=occurrence.key.position,
                selected_response_uci=occurrence.next_move,presentation_id=physical_id,
                presentation_revision=int(source["presentation_revision"]),repertoire_revision=revision,
                introduction=introduction,introduced_at=introduced_at,
                route_id=source["source_id"],route_ply=occurrence.ply,
                # Exact stored definition proves identity, not current Study
                # eligibility or unchanged historical introduction content.
                available=False,introduction_matches_current_content=False))
    payload["decision_presentations"] = [item.model_dump(mode="json") for item in mappings]
    return RepertoireSnapshot.model_validate_json(json.dumps(payload))


_default_snapshot_reader = current_snapshot
