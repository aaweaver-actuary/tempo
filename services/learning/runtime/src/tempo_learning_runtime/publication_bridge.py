"""Version-addressable Content execution adapter; Learning alone activates.

The original Content payload remains immutable. Validated aliases identify the
existing physical learning object; they never rewrite authored Content IDs.
"""
from datetime import datetime, timezone
from contextvars import ContextVar
import hashlib
import json
from typing import Literal

import chess
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator, model_serializer

from .publications import PublicationBundle, ExecutionFacts, lock_opening_repertoires
from .gateway import request_digest
from .durable_tasks import enqueue_task_in_transaction
from .clock import date
from tempo_learning_database.adapters import publication_bridge as storage

MAX_DEFINITIONS = 8192
MAX_BUNDLE_BYTES = 12 * 1024 * 1024
prepared_activation = ContextVar('prepared_content_activation',default=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def graph_routes_digest(routes):
    """Keep the existing routing identity; the full bundle binds display titles."""
    return digest([{key: value for key, value in route.items() if key != 'repertoire_title'}
                   for route in routes])


class Binding(BaseModel):
    exercise_ply: int = Field(default=0,ge=0,le=39)
    decision_id: str | None = Field(default=None,min_length=1,max_length=200)

    @model_serializer(mode='wrap')
    def preserve_prior_binding_bytes(self,handler):
        value=handler(self)
        if self.exercise_ply==0:value.pop('exercise_ply',None)
        if self.decision_id is None:value.pop('decision_id',None)
        return value

    @model_validator(mode='after')
    def explicit_projected_identity(self):
        if self.exercise_ply and self.decision_id is None:
            raise ValueError('Projected binding requires an explicit decision identity')
        return self

    model_config = ConfigDict(extra="forbid", strict=True)
    ply: int = Field(ge=0, le=1000)
    card_id: str
    reentry: bool = False


class Route(BaseModel):
    authority_start_ply: int | None = Field(default=0, ge=0, le=1000)
    repertoire_title: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator('repertoire_title')
    @classmethod
    def nonblank_repertoire_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Repertoire title must not be blank')
        return value

    @model_serializer(mode='wrap')
    def preserve_prior_routes(self, handler):
        value=handler(self)
        if self.authority_start_ply == 0:
            value.pop('authority_start_ply',None)
        if self.repertoire_title is None:
            value.pop('repertoire_title',None)
        return value

    model_config = ConfigDict(extra="forbid", strict=True)
    repertoire_id: str
    route_id: str
    policy_id: str
    learner_color: Literal["white", "black"]
    starting_fen: str
    moves: list[str] = Field(max_length=1000)
    bindings: list[Binding] = Field(max_length=1000)


def project_decision(starting_fen,accepted_lines,exercise_ply):
    """Validate the explicitly published offset without changing physical identity."""
    if type(exercise_ply) is not int or not 0<=exercise_ply<40:
        raise ValueError('Invalid decision projection offset')
    positions=set();moves=set()
    for line in accepted_lines:
        if exercise_ply>=len(line):raise ValueError('Decision projection outside accepted line')
        board=chess.Board(starting_fen)
        for move in line[:exercise_ply]:board.push_uci(move)
        positions.add(starting_fen if exercise_ply==0 else board.fen(en_passant='fen'))
        moves.add(line[exercise_ply])
    if len(positions)!=1:raise ValueError('Ambiguous decision projection position')
    return next(iter(positions)),tuple(sorted(moves))


class PublicationBundleV2(PublicationBundle):
    legacy_card_identity = False
    schema_version: Literal[2]
    facts: list[ExecutionFacts] = Field(min_length=1, max_length=MAX_DEFINITIONS)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    routes: list[Route] = Field(min_length=1, max_length=10000)
    legacy_aliases: dict[str, str] = Field(default_factory=dict, max_length=MAX_DEFINITIONS)

    @field_validator('schema_version',mode='before')
    @classmethod
    def integer_version(cls,value):
        if type(value) is not int:
            raise ValueError('schema_version must be an integer')
        return value

    @model_validator(mode="after")
    def routes_and_aliases(self):
        if self.frozen_order:
            raise ValueError("Content v2 does not author or replace frozen order")
        if self.repertoire_limits:
            raise ValueError('Content v2 does not change Learning introduction allowances')
        definitions = {item.card_id: item for item in self.publication.definitions}
        if any(item.specification.type!='move_line' or item.specification.grading_policy!='reference' for item in definitions.values()):
            raise ValueError('Content v2 openings require reference-graded accepted lines')
        facts = {item.card_id: item for item in self.facts}
        if any(item.content_type != "opening" for item in self.facts):
            raise ValueError("Content v2 currently supports opening execution; typed families retain v1")
        if any(item.parent_card_id is not None for item in self.facts):
            raise ValueError('V2 prerequisites belong to actual route occurrences')
        if not set(self.legacy_aliases) <= definitions.keys() or len(set(self.legacy_aliases.values())) != len(self.legacy_aliases):
            raise ValueError("Legacy aliases must be one-to-one accepted definitions")
        physical = [self.legacy_aliases.get(identifier, identifier) for identifier in definitions]
        if len(set(physical)) != len(physical):
            raise ValueError("Multiple Content objects cannot merge one learning object")
        seen, coverage, primary_coverage = set(), set(), set()
        repertoire_titles = {}
        owned_repertoires = {membership for fact in self.facts for membership in fact.memberships}
        decision_projections={}
        for route in self.routes:
            if route.repertoire_title is not None:
                if route.repertoire_id not in owned_repertoires:
                    raise ValueError('Repertoire title requires an accepted opening membership')
                if (route.repertoire_id in repertoire_titles and
                        repertoire_titles[route.repertoire_id] != route.repertoire_title):
                    raise ValueError('Conflicting titles for one repertoire')
                repertoire_titles[route.repertoire_id] = route.repertoire_title
            key = (route.repertoire_id, route.route_id, route.policy_id)
            if key in seen or len({binding.ply for binding in route.bindings}) != len(route.bindings):
                raise ValueError("Ambiguous route/binding")
            seen.add(key)
            board = chess.Board(route.starting_fen)
            if not board.is_valid():
                raise ValueError("Invalid route starting FEN")
            bindings = {item.ply: item for item in route.bindings}
            for ply in range(len(route.moves) + 1):
                if binding := bindings.get(ply):
                    definition = definitions.get(binding.card_id)
                    if definition is None:
                        raise ValueError("Missing route definition")
                    fact = facts[binding.card_id]
                    if route.policy_id != definition.policy_id or route.learner_color != fact.trained_color or route.repertoire_id not in fact.memberships:
                        raise ValueError("Route policy/color/membership mismatch")
                    decision_id=binding.decision_id or definition.decision_id
                    if not binding.exercise_ply and decision_id!=definition.decision_id:
                        raise ValueError('Root binding cannot relabel a decision')
                    projected_fen,accepted_moves=project_decision(definition.starting_fen,definition.specification.accepted_lines,binding.exercise_ply)
                    identity=(binding.card_id,decision_id)
                    projection=(binding.exercise_ply,projected_fen,accepted_moves)
                    if identity in decision_projections and decision_projections[identity]!=projection:
                        raise ValueError('Decision identity has ambiguous projections')
                    decision_projections[identity]=projection
                    if board.turn != (route.learner_color == "white") or board.fen() != chess.Board(projected_fen).fen():
                        raise ValueError("Binding position/color mismatch")
                    if ply == len(route.moves) or route.moves[ply] not in accepted_moves:
                        raise ValueError("Route conflicts with accepted alternatives")
                    coverage.add((binding.card_id, route.repertoire_id))
                    if decision_id==definition.decision_id:
                        primary_coverage.add((binding.card_id,route.repertoire_id))
                if ply < len(route.moves):
                    board.push_uci(route.moves[ply])
            if any(binding.ply > len(route.moves) for binding in route.bindings):
                raise ValueError("Binding outside route")
        if coverage != {(fact.card_id, membership) for fact in self.facts for membership in fact.memberships}:
            raise ValueError("Every accepted membership requires a real route binding")
        if primary_coverage!=coverage:
            raise ValueError('Every accepted membership requires its primary physical exercise decision')
        if len(self.model_dump_json().encode()) > MAX_BUNDLE_BYTES:
            raise ValueError("Publication bundle exceeds 12 MiB")
        return self


def stage(database, bundle, workspace_id):
    bundle = bundle if isinstance(bundle, PublicationBundleV2) else PublicationBundleV2.model_validate(bundle)
    publication = bundle.publication
    if publication.workspace_id != workspace_id:
        raise HTTPException(403, "Publication belongs to another workspace")
    encoded = canonical(bundle.model_dump(mode="json"))
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    storage.lock_publication(database, publication.publication_id)
    old = storage.accepted_digest(database, publication.publication_id, publication.publication_generation)
    if old and old[0] != fingerprint:
        raise HTTPException(409, "Publication identity is immutable")
    latest = storage.latest_generation(database, publication.publication_id)
    if latest and latest > publication.publication_generation:
        raise HTTPException(409, "Stale publication generation")
    storage.insert_accepted(database, publication.publication_id, publication.publication_generation, fingerprint, encoded)
    return {"status": "accepted_pending", "publication_id": publication.publication_id, "publication_generation": publication.publication_generation}


def physical_id(database, card_id):
    row = storage.physical_alias(database, card_id)
    return row[0] if row else card_id


def dispatch_activation(payload, operation_id):
    """Compute/legal validation after closing the read, before the writer locks."""
    from tempo_learning_database.connections import connection
    from . import gateway
    validate_operation_request('publication.activate.v2', payload, operation_id)
    receipt = gateway.read_operation(operation_id, publication=True)
    if receipt['state'] != 'unknown':
        if receipt['command_name'] != 'publication.activate.v2' or receipt['request'] != payload:
            from .review_conflicts import ReviewConflict
            raise ReviewConflict('command_identity_reused', 'Operation ID was already used for another request')
        if receipt['state'] in ('complete', 'failed'):
            return gateway.operation_result(operation_id)
    with connection(read_only=True) as database:
        row=storage.accepted_payload(database, payload['publication_id'], payload['publication_generation'])
    if row is None:
        raise HTTPException(409,'Publication has not been staged')
    bundle=PublicationBundleV2.model_validate_json(row[1])
    token=prepared_activation.set((row[0],bundle))
    try:
        return gateway.dispatch('publication.activate.v2',payload,operation_id)
    finally:
        prepared_activation.reset(token)


def validate_operation_request(command_name, payload, operation_id):
    if command_name not in ('publication.activate.v2', 'publication.retire'):
        raise HTTPException(422, 'Unsupported publication operation family')
    identity_field = 'activation_id' if command_name == 'publication.activate.v2' else 'retirement_id'
    required = {'publication_id', 'publication_generation', 'expected_activation_revision', identity_field}
    if (not isinstance(payload, dict) or set(payload) != required
            or any(type(payload[key]) is not int or payload[key] < (0 if key == 'expected_activation_revision' else 1)
                   for key in ('publication_generation', 'expected_activation_revision'))
            or any(not isinstance(payload[key], str) or not payload[key] for key in ('publication_id', identity_field))
            or not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128
            or operation_id != payload[identity_field]):
        raise HTTPException(422, 'Publication operation identity and exact request are required')


def abort_operation(operation_id, document, workspace_id):
    from . import gateway
    if set(document) != {'workspace_id', 'command_name', 'request'}:
        raise HTTPException(422, 'Exact publication fence identity is required')
    if document['workspace_id'] != workspace_id:
        raise HTTPException(403, 'Publication belongs to another workspace')
    validate_operation_request(document['command_name'], document['request'], operation_id)
    return gateway.fence_publication(operation_id, document['command_name'], document['request'])


def status(database, publication_id, workspace_id, generation=None):
    row = storage.serving_status(database, publication_id)
    accepted=None
    selected_generation=generation if generation is not None else row[0] if row else None
    if selected_generation is not None:
        selected=storage.accepted_payload(database, publication_id, selected_generation)
        if selected:
            document=json.loads(selected[1])
            accepted={'workspace_id':document['publication']['workspace_id'],'publication_id':publication_id,'publication_generation':selected_generation,
                'source_revision':document['publication']['source_revision'],'source_digest':document.get('source_digest'),
                'publication_digest':digest(document['publication']),'routes_digest':graph_routes_digest(document.get('routes',[])),
                'bundle_digest':selected[0],'bundle_schema_version':document['schema_version']}
    return {"schema_version": 1, "workspace_id": workspace_id, "publication_id": publication_id,'accepted':accepted,
        "local_date":date.today().isoformat(),
        "publication_generation": row[0] if row else None, "activation_revision": row[1] if row else 0,
        "status": "active" if row and row[2] == "active" else "retired" if row else "unavailable",
        "bundle_digest": row[3] if row else None, "observed_at": datetime.now(timezone.utc).isoformat()}


def activate(database, payload):
    identity, generation = payload["publication_id"], payload["publication_generation"]
    operation_id, expected = payload["activation_id"], payload["expected_activation_revision"]
    fingerprint = request_digest("publication.activate.v2", payload)
    storage.lock_publication(database, identity)
    previous = storage.activation_receipt(database, operation_id)
    if previous:
        if previous[0] != fingerprint:
            raise HTTPException(409, "Activation identity reused")
        return json.loads(previous[1])
    serving = storage.serving_for_update(database, identity)
    if (serving[1] if serving else 0) != expected or (serving and generation <= serving[0]):
        raise HTTPException(409, "Activation revision/generation changed")
    row = storage.accepted_for_update(database, identity, generation)
    latest = storage.latest_generation(database, identity)
    if row is None or row[1] != "staged" or latest != generation:
        raise HTTPException(409, "Publication is unavailable or superseded")
    prepared=prepared_activation.get()
    if prepared is None or prepared[0]!=row[2]:
        raise HTTPException(409,'Activation preparation no longer matches immutable staging')
    bundle=prepared[1]
    definitions = {item.card_id: item for item in bundle.publication.definitions}
    identifiers = {identifier: bundle.legacy_aliases.get(identifier, identifier) for identifier in definitions}
    lock_opening_repertoires(database, identity, bundle.facts)
    old_ids = {item[0] for item in storage.publication_card_ids(database, identity)}
    affected = sorted(old_ids | set(identifiers.values()))
    # One ordered SQL call acquires exactly the existing card advisory locks.
    lock_identifiers=sorted(set(affected)|set(definitions))
    storage.lock_cards(database, lock_identifiers)
    existing = {item["id"]: dict(item) for item in storage.cards_for_update(database, affected)}
    if storage.deleted_cards(database, affected):
        raise HTTPException(409, "Publication contains permanently deleted content")
    memberships = sorted({membership for fact in bundle.facts for membership in fact.memberships})
    if storage.other_opening_owner(database, memberships, identity):
        raise HTTPException(409, "Opening repertoire already has an active publication owner")
    prior_generations=sorted({item['publication_generation'] for item in existing.values() if item['publication_id']==identity})
    prior_payloads = {item[0]: json.loads(item[1]) for item in storage.accepted_generations(database, identity, prior_generations)}
    prior_definitions = {(generation,item["card_id"]): item for generation,document in prior_payloads.items() for item in document["publication"]["definitions"]}
    accepted_aliases = storage.accepted_aliases(database, list(definitions), list(identifiers.values()))
    aliases_by_content = {item[0]:tuple(item) for item in accepted_aliases}
    aliases_by_learning = {item[1]:tuple(item) for item in accepted_aliases}
    card_rows, alias_rows = [], []
    for fact in bundle.facts:
        definition = definitions[fact.card_id]
        identifier = identifiers[fact.card_id]
        old = existing.get(identifier)
        binding = digest([bundle.publication.workspace_id, identity, fact.card_id, identifier])
        aliases = (aliases_by_content.get(fact.card_id),aliases_by_learning.get(identifier))
        if any(item is not None and item != (fact.card_id,identifier,identity,binding) for item in aliases):
            raise HTTPException(409, "Historical alias already belongs to another authored object")
        if old:
            same_owner = old["publication_id"] == identity
            adoption = identifier != fact.card_id and old["publication_id"] is None
            if not (same_owner or adoption) or old["content_type"] != "opening" or old["trained_color"] != fact.trained_color:
                raise HTTPException(409, "Existing card has incompatible ownership/family/color")
            if adoption and (old["revision"] != definition.exercise_revision or old["start_fen"] != definition.starting_fen or len(definition.specification.accepted_lines)!=1 or json.loads(old["moves_json"]) != definition.specification.accepted_lines[0]):
                raise HTTPException(409, "Historical alias executable identity is incompatible")
            if same_owner:
                previous_definition = prior_definitions.get((old['publication_generation'],fact.card_id))
                if definition.exercise_revision < old["revision"] or (definition.exercise_revision == old["revision"] and (previous_definition is None or any(previous_definition[key] != definition.model_dump(mode="json")[key] for key in ("starting_fen", "specification")))):
                    raise HTTPException(409, "Executable change requires a newer revision")
        elif identifier != fact.card_id:
            raise HTTPException(409, "Historical alias requires an existing learning object")
        if identifier != fact.card_id:
            alias_rows.append({"content_card_id": fact.card_id, "learning_card_id": identifier, "publication_id": identity, "binding_digest": binding})
        card_rows.append({"id": identifier, "repertoire_id": fact.memberships[0], "start_fen": definition.starting_fen,
            "moves_json": json.dumps(definition.specification.accepted_lines[0]), "due_date": fact.due_date,
            "revision": definition.exercise_revision, "trained_color": fact.trained_color,
            "publication_id": identity, "publication_generation": generation, "pending_validation": int(not fact.integrity_allowed)})
    # Set-based writes avoid N network round trips while retaining one authoritative activation.
    repertoire_titles = {route.repertoire_id: route.repertoire_title for route in bundle.routes
                         if route.repertoire_title is not None}
    repertoire_arguments = (database, datetime.now(timezone.utc).isoformat(), memberships)
    if repertoire_titles:
        storage.insert_repertoires(*repertoire_arguments, repertoire_titles)
    else:
        storage.insert_repertoires(*repertoire_arguments)
    if alias_rows:
        storage.insert_aliases(database, alias_rows)
    storage.deactivate_cards(database, identity)
    storage.upsert_cards(database, card_rows)
    storage.remove_memberships(database, list(identifiers.values()))
    member_rows = [{"repertoire_id": membership, "card_id": identifiers[fact.card_id]} for fact in bundle.facts for membership in fact.memberships]
    storage.insert_memberships(database, member_rows)
    storage.remove_integrity_blocks(database, list(identifiers.values()))
    integrity_by_card = {identifiers[fact.card_id]: fact.integrity_allowed for fact in bundle.facts}
    blocked = [item for item in member_rows if not integrity_by_card[item["card_id"]]]
    if blocked:
        storage.insert_integrity_blocks(database, blocked)
    graph_rows, line_rows = [], []
    for route in bundle.routes:
        line_id = identity + ":" + route.route_id + ":" + route.policy_id
        line_rows.append({"id": line_id, "repertoire_id": route.repertoire_id, "trained_color": route.learner_color})
        parent = None
        decision_index=0
        for binding in sorted(route.bindings,key=lambda item:item.ply):
            definition = definitions[binding.card_id]
            if (binding.decision_id or definition.decision_id)!=definition.decision_id:
                continue
            graph_rows.append({"repertoire_id": route.repertoire_id, "generation": generation, "line_id": line_id,
                "decision_index": decision_index, "card_id": identifiers[binding.card_id], "parent_card_id": parent,
                "starting_fen": definition.starting_fen, "moves_json": json.dumps(definition.specification.accepted_lines[0]), "trained_color": route.learner_color})
            parent = identifiers[binding.card_id]
            decision_index+=1
    storage.insert_lines(database, datetime.now(timezone.utc).isoformat(), line_rows)
    storage.upsert_opening_publications(database, generation, memberships)
    storage.insert_opening_steps(database, graph_rows)
    removed = list(old_ids - set(identifiers.values()))
    revisions_by_card = {item["id"]: item["revision"] for item in card_rows}
    changed = [identifier for identifier, old in existing.items() if identifier in revisions_by_card and old["revision"] != revisions_by_card[identifier]]
    storage.supersede_card_queues(database, removed + changed)
    storage.supersede_accepted(database, identity)
    storage.activate_accepted(database, identity, generation)
    storage.upsert_serving(database, identity, generation, expected + 1)
    result = {"contract_version": 1, "workspace_id": bundle.publication.workspace_id, "activation_id": operation_id,
        "publication_id": identity, "publication_generation": generation, "activation_revision": expected + 1, "status": "activated", "reason": None}
    storage.insert_activation_receipt(database, operation_id, fingerprint, json.dumps(result))
    enqueue_task_in_transaction(database, "daily_queue", "current", {"queue_date": date.today().isoformat()})
    return result
