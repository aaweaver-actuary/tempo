"""Immutable accepted execution facts. Staging never changes serving state."""

from datetime import datetime, timezone
import hashlib, json
from typing import ClassVar, Literal
import chess
from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from tempo_content_publication_contracts.v1 import Publication
from tempo_chess_core import card_id
from .study_contracts import ExerciseSpecification
from .study_grading import validate_exercise
from .gateway import request_digest
from .durable_tasks import enqueue_task_in_transaction
from .clock import date


class ExecutionFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str
    content_type: Literal[
        "opening", "tactic", "middlegame", "endgame", "defense", "study_exercise"
    ]
    memberships: list[str] = Field(default_factory=list, max_length=100)
    trained_color: Literal["white", "black"] | None = None
    due_date: str
    parent_card_id: str | None = None
    route_order: int = Field(default=0, ge=0)
    integrity_allowed: bool = True
    legacy_specification: dict | None = None
    study_id: str | None = None
    exercise_id: str | None = None
    sibling_group: str | None = None
    source_ref: str | None = None
    source_fen: str | None = None
    endgame: dict | None = None
    defense: dict | None = None

    @field_validator("memberships")
    @classmethod
    def unique_memberships(cls, memberships):
        if len(set(memberships)) != len(memberships):
            raise ValueError("Publication memberships must be unique")
        return memberships


class PublicationBundle(BaseModel):
    legacy_card_identity: ClassVar[bool] = True
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    publication: Publication
    facts: list[ExecutionFacts] = Field(min_length=1, max_length=128)
    repertoire_limits: dict[str, int] = Field(default_factory=dict)
    frozen_order: dict | None = None

    @model_validator(mode="after")
    def complete_execution(self):
        definitions = {item.card_id: item for item in self.publication.definitions}
        if len(self.facts) != len(definitions) or {
            item.card_id for item in self.facts
        } != set(definitions):
            raise ValueError("Each definition requires exactly one execution fact")
        opening_routes = set()
        for fact in self.facts:
            definition = definitions[fact.card_id]
            board = chess.Board(definition.starting_fen)
            if not board.is_valid():
                raise ValueError("Invalid executable FEN")
            date.fromisoformat(fact.due_date)
            if fact.parent_card_id and fact.parent_card_id not in definitions:
                raise ValueError("Publication has an incomplete parent route")
            if fact.content_type == "study_exercise":
                if not (
                    fact.study_id and fact.exercise_id and fact.legacy_specification
                ):
                    raise ValueError(
                        "Typed exercise is missing its legacy identity/specification"
                    )
                specification = TypeAdapter(ExerciseSpecification).validate_python(
                    fact.legacy_specification
                )
                validate_exercise(specification, definition.starting_fen)
                # Both adapters describe the same exercise; legacy-only presentation fields may differ.
                modern = definition.specification.model_dump()
                if any(
                    fact.legacy_specification.get(key) != value
                    for key, value in modern.items()
                ):
                    raise ValueError(
                        "Legacy and publication exercise specifications disagree"
                    )
            elif not fact.memberships:
                raise ValueError("Card requires accepted membership facts")
            if fact.content_type == "opening":
                for membership in fact.memberships:
                    route = (membership, fact.route_order)
                    if route in opening_routes:
                        raise ValueError(
                            "Opening route positions must be unique per repertoire"
                        )
                    opening_routes.add(route)
            if fact.content_type in ("opening", "tactic", "middlegame"):
                if definition.specification.type != "move_line":
                    raise ValueError("Move card requires executable lines")
                for line in definition.specification.accepted_lines:
                    position = board.copy()
                    for value in line:
                        move = chess.Move.from_uci(value)
                        if move not in position.legal_moves:
                            raise ValueError("Illegal executable line")
                        position.push(move)
                    if fact.content_type == "opening" and (
                        fact.trained_color is None
                        or ("white" if position.turn else "black") == fact.trained_color
                    ):
                        raise ValueError("Opening must finish on a learner move")
                if self.legacy_card_identity and (
                    card_id(
                        definition.starting_fen,
                        definition.specification.accepted_lines[0],
                    )
                    != fact.card_id
                ):
                    raise ValueError("Card identity differs from executable definition")
            if fact.content_type == "endgame":
                if fact.endgame is None or not fact.trained_color:
                    raise ValueError("Endgame template facts missing")
                from .endgames import normalized_material

                if (
                    not {"name", "white_material", "black_material"}
                    <= fact.endgame.keys()
                ):
                    raise ValueError("Incomplete endgame template")
                white = normalized_material(fact.endgame["white_material"])
                black = normalized_material(fact.endgame["black_material"])
                if (
                    len(white) + len(black) > 7
                    or not fact.endgame.get("name")
                    or fact.endgame.get("goal_mix", "both")
                    not in ("win", "draw", "both")
                ):
                    raise ValueError("Invalid endgame template")
            if fact.content_type == "defense":
                if fact.defense is None:
                    raise ValueError("Defense rubric facts missing")
                from .defense import exercise_from_definition

                try:
                    exercise = exercise_from_definition(fact.defense)
                    if (
                        exercise.exercise_revision != definition.exercise_revision
                        or exercise.anchor.position.start_fen != definition.starting_fen
                    ):
                        raise ValueError(
                            "Defense rubric and executable definition disagree"
                        )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        "Invalid accepted defense rubric: " + str(error)
                    ) from error
        if self.frozen_order:
            if self.frozen_order.get("reader_version") != "frozen-maia-v2":
                raise ValueError("Incompatible frozen reader")
            ranks = self.frozen_order.get("ranks", [])
            if (
                not self.frozen_order.get("run_id")
                or not ranks
                or len({item["card_id"] for item in ranks}) != len(ranks)
                or len({item["ordinal"] for item in ranks}) != len(ranks)
            ):
                raise ValueError("Incomplete frozen publication")
            if any(
                item["card_id"] not in definitions
                or type(item["ordinal"]) != int
                or item["ordinal"] < 1
                for item in ranks
            ):
                raise ValueError("Invalid frozen identity or ordinal")
        if any(
            type(limit) != int or limit < 0 for limit in self.repertoire_limits.values()
        ):
            raise ValueError("Invalid repertoire allowance")
        # Cycles cannot become permanently eligible through self-reference.
        parents = {item.card_id: item.parent_card_id for item in self.facts}
        for identity in parents:
            visited = set()
            current = identity
            while current is not None:
                if current in visited:
                    raise ValueError("Cyclic publication prerequisites")
                visited.add(current)
                current = parents[current]
        return self


def stage(database, payload, workspace_id):
    bundle = PublicationBundle.model_validate(payload)
    if bundle.publication.workspace_id != workspace_id:
        raise HTTPException(403, "Publication belongs to another workspace")
    serialized = bundle.model_dump_json()
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    publication = bundle.publication
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:publication:" + publication.publication_id,),
    )
    existing = database.execute(
        "SELECT digest FROM accepted_publications WHERE publication_id=? AND generation=?",
        (publication.publication_id, publication.publication_generation),
    ).fetchone()
    if existing and existing[0] != digest:
        raise HTTPException(409, "Publication identity is immutable")
    latest = database.execute(
        "SELECT MAX(generation) FROM accepted_publications WHERE publication_id=?",
        (publication.publication_id,),
    ).fetchone()[0]
    if latest and latest > publication.publication_generation:
        raise HTTPException(409, "Stale publication generation")
    database.execute(
        "INSERT INTO accepted_publications(publication_id,generation,digest,payload_json) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
        (
            publication.publication_id,
            publication.publication_generation,
            digest,
            serialized,
        ),
    )
    return {
        "status": "accepted_pending",
        "publication_id": publication.publication_id,
        "publication_generation": publication.publication_generation,
    }


def lock_opening_repertoires(database, publication_id, new_facts=()):
    """Publication -> sorted repertoire owners -> sorted cards -> queue effects.

    Include removed memberships: a replacement/retirement releases ownership in
    the same transaction in which a successor is allowed to establish it.
    Review and admission never acquire repertoire ownership locks after cards.
    """
    repertoires = {
        row[0]
        for row in database.execute(
            "SELECT rc.repertoire_id FROM repertoire_cards rc JOIN cards c ON c.id=rc.card_id WHERE c.publication_id=? AND c.content_type='opening' UNION SELECT repertoire_id FROM cards WHERE publication_id=? AND content_type='opening' AND repertoire_id IS NOT NULL",
            (publication_id, publication_id),
        ).fetchall()
    }
    repertoires.update(
        membership
        for fact in new_facts
        if fact.content_type == "opening"
        for membership in fact.memberships
    )
    for repertoire_id in sorted(repertoires):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("learning:repertoire-owner:" + repertoire_id,),
        )


def verify_opening_graph(database, bundle):
    """Prove full graph identities and executable facts, not just cardinality."""
    expected = set()
    publication = bundle.publication
    definitions = {item.card_id: item for item in publication.definitions}
    for fact in bundle.facts:
        if fact.content_type != "opening":
            continue
        definition = definitions[fact.card_id]
        for membership in fact.memberships:
            expected.add(
                (
                    membership,
                    publication.publication_generation,
                    publication.publication_id + ":" + membership,
                    fact.route_order,
                    fact.card_id,
                    fact.parent_card_id,
                    definition.starting_fen,
                    tuple(definition.specification.accepted_lines[0]),
                    fact.trained_color,
                )
            )
    if not expected:
        return
    line_ids = sorted({step[2] for step in expected})
    actual = {
        (*tuple(row)[:7], tuple(json.loads(row[7])), row[8])
        for row in database.execute(
            "SELECT repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,starting_fen,moves_json,trained_color FROM opening_graph_steps WHERE generation=? AND line_id=ANY(?)",
            (publication.publication_generation, line_ids),
        ).fetchall()
    }
    if actual != expected:
        raise ValueError("Accepted opening graph does not match executable routes")


def activate(database, payload):
    identity = payload["publication_id"]
    generation = payload["publication_generation"]
    expected = payload["expected_activation_revision"]
    operation_id = payload["activation_id"]
    digest = request_digest("publication.activate", payload)
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:publication:" + identity,),
    )
    prior = database.execute(
        "SELECT request_hash,result_json FROM activation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if prior:
        if prior[0] != digest:
            raise HTTPException(409, "Activation identity reused")
        return json.loads(prior[1])
    serving = database.execute(
        "SELECT generation,activation_revision FROM serving_publications WHERE publication_id=? FOR UPDATE",
        (identity,),
    ).fetchone()
    if (serving[1] if serving else 0) != expected:
        raise HTTPException(409, "Activation revision changed")
    if serving and generation <= serving[0]:
        raise HTTPException(409, "Stale publication generation")
    row = database.execute(
        "SELECT payload_json,state FROM accepted_publications WHERE publication_id=? AND generation=? FOR UPDATE",
        (identity, generation),
    ).fetchone()
    if row is None or row[1] != "staged":
        raise HTTPException(409, "Publication is unavailable for activation")
    latest = database.execute(
        "SELECT MAX(generation) FROM accepted_publications WHERE publication_id=?",
        (identity,),
    ).fetchone()[0]
    if generation != latest:
        raise HTTPException(
            409, "A newer staged publication supersedes this generation"
        )
    bundle = PublicationBundle.model_validate_json(row[0])
    lock_opening_repertoires(database, identity, bundle.facts)
    definitions = {item.card_id: item for item in bundle.publication.definitions}
    now = datetime.now(timezone.utc).isoformat()
    # Fence review and publication in the same stable card-before-queue lock order.
    old_ids = {
        item[0]
        for item in database.execute(
            "SELECT id FROM cards WHERE publication_id=?", (identity,)
        ).fetchall()
    }
    new_ids = set(definitions)
    for identifier in sorted(old_ids | new_ids):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:card-edit:" + identifier,),
        )
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (identifier,))
    if database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=ANY(?) LIMIT 1", (list(new_ids),)
    ).fetchone():
        raise HTTPException(409, "Publication contains permanently deleted content")
    database.execute(
        "UPDATE cards SET serving_active=0 WHERE publication_id=?", (identity,)
    )
    for fact in sorted(bundle.facts, key=lambda item: item.parent_card_id is not None):
        definition = definitions[fact.card_id]
        conflicting = database.execute(
            "SELECT publication_id,revision,content_type,study_exercise_id,source_ref FROM cards WHERE id=?",
            (fact.card_id,),
        ).fetchone()
        if conflicting and (
            conflicting[0] != identity
            or definition.exercise_revision <= conflicting[1]
            or conflicting[2] != fact.content_type
            or conflicting[3] != fact.exercise_id
            or conflicting[4] != fact.source_ref
        ):
            raise HTTPException(
                409,
                "Existing executable identity needs a newer revision in its owning publication",
            )
        if fact.defense:
            previous_defense = database.execute(
                "SELECT candidate_id FROM accepted_defense_exercises WHERE card_id=?",
                (fact.card_id,),
            ).fetchone()
            if previous_defense and previous_defense[0] != fact.defense["candidate_id"]:
                raise HTTPException(
                    409, "Defense identity changed within an executable card"
                )
        for membership in fact.memberships:
            if (
                fact.content_type == "opening"
                and database.execute(
                    "SELECT 1 FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.publication_id!=? AND c.serving_active=1 LIMIT 1",
                    (membership, identity),
                ).fetchone()
            ):
                raise HTTPException(
                    409, "Opening repertoire already has an active publication owner"
                )
            database.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,?,?, ?,?) ON CONFLICT(id) DO UPDATE SET new_cards_per_day=excluded.new_cards_per_day",
                (
                    membership,
                    membership,
                    "Accepted publication",
                    now,
                    bundle.repertoire_limits.get(membership),
                ),
            )
        if fact.content_type == "study_exercise":
            database.execute(
                "INSERT INTO studies(id,title) VALUES(?,?) ON CONFLICT DO NOTHING",
                (fact.study_id, fact.study_id),
            )
            database.execute(
                "INSERT INTO study_positions(id,fen) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET fen=excluded.fen",
                (fact.exercise_id, definition.starting_fen),
            )
            database.execute(
                "INSERT INTO study_exercises(id,study_id,position_id,current_revision,status,sibling_group) VALUES(?,?,?,?,'published',?) ON CONFLICT(id) DO UPDATE SET current_revision=excluded.current_revision,status='published',sibling_group=excluded.sibling_group",
                (
                    fact.exercise_id,
                    fact.study_id,
                    fact.exercise_id,
                    definition.exercise_revision,
                    fact.sibling_group,
                ),
            )
            encoded = json.dumps(fact.legacy_specification)
            database.execute(
                "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    fact.exercise_id,
                    definition.exercise_revision,
                    encoded,
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    now,
                ),
            )
        moves = (
            definition.specification.accepted_lines[0]
            if definition.specification.type == "move_line"
            else []
        )
        database.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,revision,trained_color,study_exercise_id,source_ref,source_fen,publication_id,publication_generation,serving_active)
           VALUES(?,?,?,?,?,'new',?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
             start_fen=excluded.start_fen,moves_json=excluded.moves_json,revision=excluded.revision,trained_color=excluded.trained_color,
             publication_generation=excluded.publication_generation,serving_active=1""",
            (
                fact.card_id,
                fact.memberships[0] if fact.memberships else None,
                "exercise" if fact.content_type == "study_exercise" else "checkpoint",
                definition.starting_fen,
                json.dumps(moves),
                fact.due_date,
                fact.content_type,
                definition.exercise_revision,
                fact.trained_color,
                fact.exercise_id,
                fact.source_ref,
                fact.source_fen,
                identity,
                generation,
            ),
        )
        database.execute(
            "UPDATE cards SET pending_validation=? WHERE id=?",
            (int(not fact.integrity_allowed), fact.card_id),
        )
        database.execute(
            "DELETE FROM repertoire_cards WHERE card_id=?", (fact.card_id,)
        )
        database.execute(
            "DELETE FROM repertoire_integrity_card_blocks WHERE card_id=?",
            (fact.card_id,),
        )
        for membership in fact.memberships:
            database.execute(
                "INSERT INTO repertoire_cards VALUES(?,?)", (membership, fact.card_id)
            )
            if not fact.integrity_allowed:
                database.execute(
                    "INSERT INTO repertoire_integrity_card_blocks VALUES(?,?)",
                    (membership, fact.card_id),
                )
            if fact.content_type == "opening":
                line_id = identity + ":" + membership
                database.execute(
                    "INSERT INTO repertoire_lines(id,repertoire_id,trained_color,created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                    (line_id, membership, fact.trained_color, now),
                )
                database.execute(
                    "INSERT INTO opening_graph_publications VALUES(?,?,'ready') ON CONFLICT(repertoire_id) DO UPDATE SET generation=excluded.generation,state='ready'",
                    (membership, generation),
                )
                database.execute(
                    "INSERT INTO opening_graph_steps VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        membership,
                        generation,
                        line_id,
                        fact.route_order,
                        fact.card_id,
                        fact.parent_card_id,
                        definition.starting_fen,
                        json.dumps(moves),
                        fact.trained_color,
                    ),
                )
        if fact.endgame:
            template = fact.endgame
            database.execute(
                "INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,white_material=excluded.white_material,black_material=excluded.black_material,trained_color=excluded.trained_color,goal_mix=excluded.goal_mix,enabled=1",
                (
                    fact.source_ref or fact.card_id,
                    fact.card_id,
                    template["name"],
                    template["white_material"],
                    template["black_material"],
                    fact.trained_color,
                    template.get("goal_mix", "both"),
                    now,
                ),
            )
        if fact.defense:
            database.execute(
                "INSERT INTO accepted_defense_exercises VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET exercise_revision=excluded.exercise_revision,definition_json=excluded.definition_json",
                (
                    fact.defense["candidate_id"],
                    fact.card_id,
                    definition.exercise_revision,
                    json.dumps(fact.defense),
                ),
            )
    verify_opening_graph(database, bundle)
    if bundle.frozen_order:
        from .frozen_maia_math import content_fingerprint

        frozen = bundle.frozen_order
        database.execute(
            "SELECT id FROM frozen_opening_activation WHERE id=1 FOR UPDATE"
        )
        if database.execute(
            "SELECT 1 FROM frozen_opening_runs WHERE run_id=?", (frozen["run_id"],)
        ).fetchone():
            raise HTTPException(409, "Frozen publication identities are immutable")
        database.execute(
            "INSERT INTO frozen_opening_runs VALUES(?,?)",
            (frozen["run_id"], frozen["reader_version"]),
        )
        for rank in frozen["ranks"]:
            card = dict(
                database.execute(
                    "SELECT * FROM cards WHERE id=?", (rank["card_id"],)
                ).fetchone()
            )
            memberships = sorted(
                {
                    row[0]
                    for row in database.execute(
                        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=?",
                        (rank["card_id"],),
                    ).fetchall()
                }
            )
            fingerprint = content_fingerprint(card, memberships)
            if rank["fingerprint"] != fingerprint:
                raise HTTPException(409, "Frozen definition fingerprint mismatch")
            definition = {
                field: card[field]
                for field in (
                    "revision",
                    "start_fen",
                    "moves_json",
                    "trained_color",
                    "source_ref",
                    "source_fen",
                    "kind",
                    "content_type",
                )
            }
            definition["membership_count"] = len(memberships)
            database.execute(
                "INSERT INTO frozen_opening_ranks VALUES(?,?,?,?,?,?)",
                (
                    frozen["run_id"],
                    rank["card_id"],
                    rank["ordinal"],
                    fingerprint,
                    json.dumps(memberships),
                    json.dumps(definition),
                ),
            )
        database.execute(
            "UPDATE frozen_opening_activation SET run_id=?,epoch=epoch+1 WHERE id=1",
            (frozen["run_id"],),
        )
    for identifier in old_ids - new_ids:
        database.execute(
            "UPDATE daily_queue SET status='superseded' WHERE card_id=? AND status='queued'",
            (identifier,),
        )
    database.execute(
        "UPDATE accepted_publications SET state='active' WHERE publication_id=? AND generation=?",
        (identity, generation),
    )
    database.execute(
        "INSERT INTO serving_publications VALUES(?,?,?) ON CONFLICT(publication_id) DO UPDATE SET generation=excluded.generation,activation_revision=excluded.activation_revision",
        (identity, generation, expected + 1),
    )
    result = {
        "status": "activated",
        "publication_id": identity,
        "publication_generation": generation,
        "activation_revision": expected + 1,
    }
    database.execute(
        "INSERT INTO activation_receipts VALUES(?,?,?)",
        (operation_id, digest, json.dumps(result)),
    )
    enqueue_task_in_transaction(
        database, "daily_queue", "current", {"queue_date": date.today().isoformat()}
    )
    return result


def retire(database, payload):
    identity = payload["publication_id"]
    generation = payload["publication_generation"]
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:publication:" + identity,),
    )
    serving = database.execute(
        "SELECT generation,activation_revision FROM serving_publications WHERE publication_id=? FOR UPDATE",
        (identity,),
    ).fetchone()
    if not serving or tuple(serving) != (
        generation,
        payload["expected_activation_revision"],
    ):
        raise HTTPException(409, "Retirement revision changed")
    lock_opening_repertoires(database, identity)
    for row in database.execute(
        "SELECT id FROM cards WHERE publication_id=? ORDER BY id", (identity,)
    ).fetchall():
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:card-edit:" + row[0],),
        )
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (row[0],))
    database.execute(
        "UPDATE cards SET serving_active=0 WHERE publication_id=?", (identity,)
    )
    database.execute(
        "UPDATE study_exercises SET status='archived' WHERE id IN (SELECT study_exercise_id FROM cards WHERE publication_id=?)",
        (identity,),
    )
    database.execute(
        "UPDATE endgame_templates SET enabled=0 WHERE card_id IN (SELECT id FROM cards WHERE publication_id=?)",
        (identity,),
    )
    database.execute(
        "UPDATE daily_queue SET status='superseded' WHERE status='queued' AND card_id IN (SELECT id FROM cards WHERE publication_id=?)",
        (identity,),
    )
    database.execute(
        "UPDATE accepted_publications SET state='retired' WHERE publication_id=? AND generation=?",
        (identity, generation),
    )
    database.execute(
        "UPDATE serving_publications SET activation_revision=activation_revision+1 WHERE publication_id=?",
        (identity,),
    )
    return {
        "status": "retired",
        "stale_attempts_fenced": True,
        "activation_revision": serving[1] + 1,
    }
