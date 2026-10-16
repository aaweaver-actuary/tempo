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
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from psycopg.types.json import Jsonb

from .publications import PublicationBundle, ExecutionFacts, lock_opening_repertoires
from .gateway import request_digest
from .durable_tasks import enqueue_task_in_transaction
from .clock import date

MAX_DEFINITIONS = 2048
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
prepared_activation = ContextVar('prepared_content_activation',default=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ply: int = Field(ge=0, le=1000)
    card_id: str
    reentry: bool = False


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repertoire_id: str
    route_id: str
    policy_id: str
    learner_color: Literal["white", "black"]
    starting_fen: str
    moves: list[str] = Field(max_length=1000)
    bindings: list[Binding] = Field(max_length=1000)


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
        seen, coverage = set(), set()
        for route in self.routes:
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
                    if board.turn != (route.learner_color == "white") or board.fen() != chess.Board(definition.starting_fen).fen():
                        raise ValueError("Binding position/color mismatch")
                    if ply == len(route.moves) or route.moves[ply] not in {line[0] for line in definition.specification.accepted_lines}:
                        raise ValueError("Route conflicts with accepted alternatives")
                    coverage.add((binding.card_id, route.repertoire_id))
                if ply < len(route.moves):
                    board.push_uci(route.moves[ply])
            if any(binding.ply > len(route.moves) for binding in route.bindings):
                raise ValueError("Binding outside route")
        if coverage != {(fact.card_id, membership) for fact in self.facts for membership in fact.memberships}:
            raise ValueError("Every accepted membership requires a real route binding")
        if len(self.model_dump_json().encode()) > MAX_BUNDLE_BYTES:
            raise ValueError("Publication bundle exceeds 8 MiB")
        return self


def stage(database, bundle, workspace_id):
    bundle = bundle if isinstance(bundle, PublicationBundleV2) else PublicationBundleV2.model_validate(bundle)
    publication = bundle.publication
    if publication.workspace_id != workspace_id:
        raise HTTPException(403, "Publication belongs to another workspace")
    encoded = canonical(bundle.model_dump(mode="json"))
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("learning:publication:" + publication.publication_id,))
    old = database.execute("SELECT digest FROM accepted_publications WHERE publication_id=? AND generation=?", (publication.publication_id, publication.publication_generation)).fetchone()
    if old and old[0] != fingerprint:
        raise HTTPException(409, "Publication identity is immutable")
    latest = database.execute("SELECT MAX(generation) FROM accepted_publications WHERE publication_id=?", (publication.publication_id,)).fetchone()[0]
    if latest and latest > publication.publication_generation:
        raise HTTPException(409, "Stale publication generation")
    database.execute("INSERT INTO accepted_publications(publication_id,generation,digest,payload_json) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
        (publication.publication_id, publication.publication_generation, fingerprint, encoded))
    return {"status": "accepted_pending", "publication_id": publication.publication_id, "publication_generation": publication.publication_generation}


def physical_id(database, card_id):
    row = database.execute("SELECT learning_card_id FROM content_card_aliases WHERE content_card_id=?", (card_id,)).fetchone()
    return row[0] if row else card_id


def dispatch_activation(payload, operation_id):
    """Compute/legal validation after closing the read, before the writer locks."""
    from .database import connection
    from . import gateway
    with connection(read_only=True) as database:
        row=database.execute('SELECT digest,payload_json FROM accepted_publications WHERE publication_id=? AND generation=?',
            (payload['publication_id'],payload['publication_generation'])).fetchone()
    if row is None:
        raise HTTPException(409,'Publication has not been staged')
    bundle=PublicationBundleV2.model_validate_json(row[1])
    token=prepared_activation.set((row[0],bundle))
    try:
        return gateway.dispatch('publication.activate.v2',payload,operation_id)
    finally:
        prepared_activation.reset(token)


def status(database, publication_id, workspace_id, generation=None):
    row = database.execute("SELECT serving.generation,serving.activation_revision,accepted.state,accepted.digest FROM serving_publications serving JOIN accepted_publications accepted ON accepted.publication_id=serving.publication_id AND accepted.generation=serving.generation WHERE serving.publication_id=?", (publication_id,)).fetchone()
    accepted=None
    selected_generation=generation if generation is not None else row[0] if row else None
    if selected_generation is not None:
        selected=database.execute('SELECT digest,payload_json FROM accepted_publications WHERE publication_id=? AND generation=?',(publication_id,selected_generation)).fetchone()
        if selected:
            document=json.loads(selected[1])
            accepted={'workspace_id':document['publication']['workspace_id'],'publication_id':publication_id,'publication_generation':selected_generation,
                'source_revision':document['publication']['source_revision'],'source_digest':document.get('source_digest'),
                'publication_digest':digest(document['publication']),'routes_digest':digest(document.get('routes',[])),
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
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("learning:publication:" + identity,))
    previous = database.execute("SELECT request_hash,result_json FROM activation_receipts WHERE operation_id=?", (operation_id,)).fetchone()
    if previous:
        if previous[0] != fingerprint:
            raise HTTPException(409, "Activation identity reused")
        return json.loads(previous[1])
    serving = database.execute("SELECT generation,activation_revision FROM serving_publications WHERE publication_id=? FOR UPDATE", (identity,)).fetchone()
    if (serving[1] if serving else 0) != expected or (serving and generation <= serving[0]):
        raise HTTPException(409, "Activation revision/generation changed")
    row = database.execute("SELECT payload_json,state,digest FROM accepted_publications WHERE publication_id=? AND generation=? FOR UPDATE", (identity, generation)).fetchone()
    latest = database.execute("SELECT MAX(generation) FROM accepted_publications WHERE publication_id=?", (identity,)).fetchone()[0]
    if row is None or row[1] != "staged" or latest != generation:
        raise HTTPException(409, "Publication is unavailable or superseded")
    prepared=prepared_activation.get()
    if prepared is None or prepared[0]!=row[2]:
        raise HTTPException(409,'Activation preparation no longer matches immutable staging')
    bundle=prepared[1]
    definitions = {item.card_id: item for item in bundle.publication.definitions}
    identifiers = {identifier: bundle.legacy_aliases.get(identifier, identifier) for identifier in definitions}
    lock_opening_repertoires(database, identity, bundle.facts)
    old_ids = {item[0] for item in database.execute("SELECT id FROM cards WHERE publication_id=?", (identity,)).fetchall()}
    affected = sorted(old_ids | set(identifiers.values()))
    # One ordered SQL call acquires exactly the existing card advisory locks.
    lock_identifiers=sorted(set(affected)|set(definitions))
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended('tempo:card-edit:' || card,0)) FROM (SELECT unnest(%s::text[]) AS card ORDER BY card) ordered", (lock_identifiers,)).fetchall()
    existing = {item["id"]: dict(item) for item in database.execute("SELECT * FROM cards WHERE id=ANY(?) ORDER BY id FOR UPDATE", (affected,)).fetchall()}
    if database.execute("SELECT 1 FROM deleted_cards WHERE card_id=ANY(?) LIMIT 1", (affected,)).fetchone():
        raise HTTPException(409, "Publication contains permanently deleted content")
    memberships = sorted({membership for fact in bundle.facts for membership in fact.memberships})
    if database.execute("SELECT 1 FROM cards card JOIN repertoire_cards membership ON membership.card_id=card.id WHERE membership.repertoire_id=ANY(?) AND card.content_type='opening' AND card.serving_active=1 AND card.publication_id IS DISTINCT FROM ? LIMIT 1", (memberships, identity)).fetchone():
        raise HTTPException(409, "Opening repertoire already has an active publication owner")
    prior_generations=sorted({item['publication_generation'] for item in existing.values() if item['publication_id']==identity})
    prior_payloads = {item[0]: json.loads(item[1]) for item in database.execute("SELECT generation,payload_json FROM accepted_publications WHERE publication_id=? AND generation=ANY(?)", (identity,prior_generations)).fetchall()}
    prior_definitions = {(generation,item["card_id"]): item for generation,document in prior_payloads.items() for item in document["publication"]["definitions"]}
    accepted_aliases = database.execute('SELECT content_card_id,learning_card_id,publication_id,binding_digest FROM content_card_aliases WHERE content_card_id=ANY(?) OR learning_card_id=ANY(?)',
                                       (list(definitions),list(identifiers.values()))).fetchall()
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
    database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) SELECT value,value,'Accepted Content publication',%s FROM unnest(%s::text[]) value ON CONFLICT DO NOTHING", (datetime.now(timezone.utc).isoformat(), memberships))
    if alias_rows:
        database.execute_native("INSERT INTO content_card_aliases SELECT * FROM jsonb_to_recordset(%s) AS x(content_card_id text,learning_card_id text,publication_id text,binding_digest text) ON CONFLICT DO NOTHING", (Jsonb(alias_rows),))
    database.execute("UPDATE cards SET serving_active=0 WHERE publication_id=?", (identity,))
    database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,revision,trained_color,publication_id,publication_generation,serving_active,pending_validation)
        SELECT id,repertoire_id,'checkpoint',start_fen,moves_json,'new',due_date,'opening',revision,trained_color,publication_id,publication_generation,1,pending_validation
        FROM jsonb_to_recordset(%s) AS x(id text,repertoire_id text,start_fen text,moves_json text,due_date text,revision bigint,trained_color text,publication_id text,publication_generation bigint,pending_validation integer)
        ON CONFLICT(id) DO UPDATE SET repertoire_id=excluded.repertoire_id,start_fen=excluded.start_fen,moves_json=excluded.moves_json,revision=excluded.revision,trained_color=excluded.trained_color,publication_id=excluded.publication_id,publication_generation=excluded.publication_generation,serving_active=1,pending_validation=excluded.pending_validation""", (Jsonb(card_rows),))
    database.execute("DELETE FROM repertoire_cards WHERE card_id=ANY(?)", (list(identifiers.values()),))
    member_rows = [{"repertoire_id": membership, "card_id": identifiers[fact.card_id]} for fact in bundle.facts for membership in fact.memberships]
    database.execute_native("INSERT INTO repertoire_cards SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,card_id text)", (Jsonb(member_rows),))
    database.execute("DELETE FROM repertoire_integrity_card_blocks WHERE card_id=ANY(?)", (list(identifiers.values()),))
    blocked = [item for item in member_rows if not next(fact for fact in bundle.facts if identifiers[fact.card_id] == item["card_id"]).integrity_allowed]
    if blocked:
        database.execute_native("INSERT INTO repertoire_integrity_card_blocks SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,card_id text)", (Jsonb(blocked),))
    graph_rows, line_rows = [], []
    for route in bundle.routes:
        line_id = identity + ":" + route.route_id + ":" + route.policy_id
        line_rows.append({"id": line_id, "repertoire_id": route.repertoire_id, "trained_color": route.learner_color})
        parent = None
        for decision_index, binding in enumerate(sorted(route.bindings, key=lambda item: item.ply)):
            definition = definitions[binding.card_id]
            graph_rows.append({"repertoire_id": route.repertoire_id, "generation": generation, "line_id": line_id,
                "decision_index": decision_index, "card_id": identifiers[binding.card_id], "parent_card_id": parent,
                "starting_fen": definition.starting_fen, "moves_json": json.dumps(definition.specification.accepted_lines[0]), "trained_color": route.learner_color})
            parent = identifiers[binding.card_id]
    database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,trained_color,created_at) SELECT id,repertoire_id,trained_color,%s FROM jsonb_to_recordset(%s) AS x(id text,repertoire_id text,trained_color text) ON CONFLICT DO NOTHING", (datetime.now(timezone.utc).isoformat(), Jsonb(line_rows)))
    database.execute_native("INSERT INTO opening_graph_publications SELECT value,%s,'ready' FROM unnest(%s::text[]) value ON CONFLICT(repertoire_id) DO UPDATE SET generation=excluded.generation,state='ready'", (generation, memberships))
    database.execute_native("INSERT INTO opening_graph_steps SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,generation bigint,line_id text,decision_index bigint,card_id text,parent_card_id text,starting_fen text,moves_json text,trained_color text)", (Jsonb(graph_rows),))
    removed = list(old_ids - set(identifiers.values()))
    changed = [identifier for identifier, old in existing.items() if identifier in identifiers.values() and old["revision"] != next(item["revision"] for item in card_rows if item["id"] == identifier)]
    database.execute("UPDATE daily_queue SET status='superseded' WHERE status='queued' AND card_id=ANY(?)", (removed + changed,))
    database.execute("UPDATE accepted_publications SET state='superseded' WHERE publication_id=? AND state='active'", (identity,))
    database.execute("UPDATE accepted_publications SET state='active' WHERE publication_id=? AND generation=?", (identity, generation))
    database.execute("INSERT INTO serving_publications VALUES(?,?,?) ON CONFLICT(publication_id) DO UPDATE SET generation=excluded.generation,activation_revision=excluded.activation_revision", (identity, generation, expected + 1))
    result = {"contract_version": 1, "workspace_id": bundle.publication.workspace_id, "activation_id": operation_id,
        "publication_id": identity, "publication_generation": generation, "activation_revision": expected + 1, "status": "activated", "reason": None}
    database.execute("INSERT INTO activation_receipts VALUES(?,?,?)", (operation_id, fingerprint, json.dumps(result)))
    enqueue_task_in_transaction(database, "daily_queue", "current", {"queue_date": date.today().isoformat()})
    return result
