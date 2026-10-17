"""Explicit local snapshot adapter; this is not an extension of shared wire v1."""
from typing import Literal
import hashlib
import base64
import zlib
import json

import chess
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator, model_serializer
from tempo_chess_core import canonical_fen_key
from tempo_content_publication_contracts.v1 import Publication, ActivationAcknowledgement


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator('schema_version',mode='before',check_fields=False)
    @classmethod
    def integer_version(cls,value):
        if type(value) is not int:
            raise ValueError('schema_version must be an integer')
        return value


class Binding(StrictModel):
    ply: int = Field(ge=0, le=1000)
    card_id: str = Field(min_length=1)
    reentry: bool = False


class Route(StrictModel):
    authority_start_ply: int | None = Field(default=0, ge=0, le=1000)

    @model_serializer(mode='wrap')
    def preserve_prior_routes(self, handler):
        value=handler(self)
        if self.authority_start_ply == 0:
            value.pop('authority_start_ply',None)
        return value

    repertoire_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    learner_color: Literal["white", "black"]
    starting_fen: str
    moves: list[str] = Field(max_length=1000)
    bindings: list[Binding] = Field(max_length=1000)


class ComparisonSnapshot(StrictModel):
    schema_version: Literal[1]
    complete: bool = False
    snapshot_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    publication: Publication
    activation: ActivationAcknowledgement | None
    routes: list[Route] = Field(max_length=10000)

    def route_identity(self, route):
        return (route.repertoire_id, route.route_id)

    def serving_current(self):
        return self.activation is not None

    @model_validator(mode="after")
    def verified_routes(self):
        publication = self.publication
        if self.activation and (
            self.activation.status != "activated" or
            (self.activation.workspace_id, self.activation.publication_id, self.activation.publication_generation)
            != (publication.workspace_id, publication.publication_id, publication.publication_generation)
        ):
            raise ValueError("snapshot_activation_does_not_match_publication")
        definitions = {item.card_id: item for item in publication.definitions}
        seen_routes = set()
        for route in self.routes:
            route_key = self.route_identity(route)
            if route_key in seen_routes:
                raise ValueError("duplicate_route_identity")
            seen_routes.add(route_key)
            if len({item.ply for item in route.bindings}) != len(route.bindings):
                raise ValueError("ambiguous_route_binding")
            board = chess.Board(route.starting_fen)
            if not board.is_valid():
                raise ValueError("invalid_route_board")
            bindings = {item.ply: item for item in route.bindings}
            for ply in range(len(route.moves) + 1):
                if ply in bindings:
                    definition = definitions.get(bindings[ply].card_id)
                    if definition is None or canonical_fen_key(board.fen()) != canonical_fen_key(chess.Board(definition.starting_fen).fen()):
                        raise ValueError("binding_missing_definition_or_wrong_position")
                    if board.turn != (route.learner_color == "white"):
                        raise ValueError("binding_is_opponent_move")
                    spec = definition.specification
                    if spec.type == "move_line":
                        if (spec.grading_policy == "reference" and ply<len(route.moves)
                                and route.moves[ply] not in {line[0] for line in spec.accepted_lines}):
                            raise ValueError("route_conflicts_with_accepted_policy")
                        for line in spec.accepted_lines:
                            alternative_board = board.copy()
                            for uci in line:
                                alternative_board.push_uci(uci)
                if ply < len(route.moves):
                    board.push_uci(route.moves[ply])
            if any(binding.ply > len(route.moves) for binding in route.bindings):
                raise ValueError("binding_outside_route")
        return self

    def compiled_routes(self):
        compiled = []
        for route in self.routes:
            board = chess.Board(route.starting_fen)
            keys = [canonical_fen_key(board.fen(en_passant="legal"))]
            for uci in route.moves:
                board.push_uci(uci)
                keys.append(canonical_fen_key(board.fen(en_passant="legal")))
            compiled.append((route, keys, {item.ply: item for item in route.bindings}))
        return compiled


class RouteV2(Route):
    policy_id: str = Field(min_length=1)


class ComparisonSnapshotV2(ComparisonSnapshot):
    schema_version: Literal[2]
    routes: list[RouteV2] = Field(max_length=10000)
    content_export: dict
    current_serving: dict
    activation_origin: dict
    acceptance: dict

    def route_identity(self, route):
        return (route.repertoire_id, route.route_id, route.policy_id)

    def serving_current(self):
        current = self.current_serving
        return bool(self.activation and current.get("status") == "active" and
                    current.get("publication_generation") == self.publication.publication_generation and
                    current.get("activation_revision") == self.activation.activation_revision)

    @model_validator(mode="after")
    def coherent_export(self):
        export = self.content_export
        def canonical_digest(value):
            return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        encoded = json.dumps({key: value for key, value in export.items() if key != "export_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if export.get("kind") != "content_bridge_export" or export.get("schema_version") != 1 or hashlib.sha256(encoded.encode()).hexdigest() != export.get("export_digest"):
            raise ValueError("content_export_digest_mismatch")
        if export.get("publication") != self.publication.model_dump(mode="json") or export.get("workspace_id") != self.publication.workspace_id or export.get("source", {}).get("source_revision") != self.publication.source_revision or export.get("complete") != self.complete:
            raise ValueError("snapshot_source_publication_generation_mix")
        expected_routes = [{key: value for key, value in route.items() if key in RouteV2.model_fields} for route in export["routes"]]
        if expected_routes != [route.model_dump(mode="json") for route in self.routes]:
            raise ValueError("snapshot_route_generation_mix")
        acceptance=self.acceptance
        if (acceptance.get('workspace_id'),acceptance.get('publication_id'),acceptance.get('publication_generation'),acceptance.get('source_revision'),acceptance.get('source_digest'),acceptance.get('bundle_schema_version'))!=(self.publication.workspace_id,self.publication.publication_id,self.publication.publication_generation,self.publication.source_revision,export['source']['source_digest'],2) or acceptance.get('publication_digest')!=canonical_digest(export['publication']) or acceptance.get('routes_digest')!=canonical_digest(expected_routes):
            raise ValueError('snapshot_accepted_source_or_routes_mismatch')
        if self.activation_origin.get("transport") != "authenticated_learning_http" or not self.activation_origin.get("endpoint"):
            raise ValueError("activation_transport_provenance_missing")
        if self.activation is None or (self.current_serving.get("workspace_id"), self.current_serving.get("publication_id")) != (self.publication.workspace_id, self.publication.publication_id):
            raise ValueError("snapshot_serving_scope_mismatch")
        definitions = {item.card_id: item for item in self.publication.definitions}
        if any(definitions[binding.card_id].policy_id != route.policy_id for route in self.routes for binding in route.bindings):
            raise ValueError("snapshot_binding_policy_mismatch")
        expected_positions=set()
        for route in self.routes:
            board=chess.Board(route.starting_fen)
            for ply in range(len(route.moves)+1):
                if board.turn != (route.learner_color=='white'):
                    expected_positions.add(canonical_fen_key(board.fen(en_passant='legal')))
                if ply<len(route.moves):
                    board.push_uci(route.moves[ply])
        if len(export['legal_replies'])!=len(expected_positions) or {item['position_key'] for item in export['legal_replies']}!=expected_positions:
            raise ValueError('legal_reply_inventory_missing')
        for inventory in export["legal_replies"]:
            board = chess.Board(inventory["position_key"] + " 0 1")
            if inventory.get("generation") != self.publication.publication_generation or inventory.get("complete") is not True or inventory.get("moves") != sorted(move.uci() for move in board.legal_moves):
                raise ValueError("legal_reply_inventory_incomplete_or_mixed")
        self.verify_response_scopes(canonical_digest)
        return self

    def verify_response_scopes(self, canonical_digest):
        """Reconstruct response authority from the accepted routes and definitions."""
        expected = {}
        definitions = {item.card_id: item.model_dump(mode='json') for item in self.publication.definitions}
        for route in self.routes:
            board = chess.Board(route.starting_fen)
            for ply in range(len(route.moves) + 1):
                if board.turn != (route.learner_color == 'white'):
                    identity = (route.repertoire_id, route.policy_id, route.learner_color,
                                route.starting_fen, tuple(route.moves[:ply]))
                    expected.setdefault(identity, []).append((route, ply, board.copy(stack=False)))
                if ply < len(route.moves):
                    board.push_uci(route.moves[ply])
        seen = set()
        for inventory in self.content_export['legal_replies']:
            for scope in inventory.get('scopes', []):
                identity = (scope.get('repertoire_id'), scope.get('policy_id'), scope.get('learner_color'),
                            scope.get('starting_fen'), tuple(scope.get('history', [])))
                occurrences = expected.get(identity)
                if not occurrences or identity in seen:
                    raise ValueError('response_scope_missing_or_ambiguous')
                seen.add(identity)
                if scope.get('digest') != canonical_digest({key:value for key,value in scope.items() if key != 'digest'}):
                    raise ValueError('response_scope_digest_mismatch')
                complete = self.complete and all(
                    route.authority_start_ply is not None and ply >= route.authority_start_ply and ply < len(route.moves) and any(binding.ply == ply + 1 for binding in route.bindings)
                    for route, ply, _ in occurrences)
                actual_nodes = [(item.get('route_id'),item.get('ply')) for item in scope.get('route_contexts', [])]
                expected_nodes = [(route.route_id,ply) for route,ply,_ in occurrences]
                board = occurrences[0][2]
                if (len(actual_nodes) != len(expected_nodes) or set(actual_nodes) != set(expected_nodes)
                        or canonical_fen_key(board.fen()) != inventory['position_key']
                        or type(scope.get('complete')) is not bool or scope['complete'] != complete
                        or scope.get('source_revision') != self.publication.source_revision
                        or scope.get('publication_generation') != self.publication.publication_generation):
                    raise ValueError('response_scope_generation_or_route_mix')
                replies = scope.get('replies', [])
                if [item.get('move_uci') for item in replies] != inventory['moves']:
                    raise ValueError('response_scope_replies_incomplete')
                for reply in replies:
                    move = reply['move_uci']
                    accepted = {binding.card_id:definitions[binding.card_id]
                                for route,ply,_ in occurrences if ply < len(route.moves) and route.moves[ply] == move
                                for binding in route.bindings if binding.ply == ply + 1}
                    decision_ids = {item['decision_id'] for item in accepted.values()}
                    status = 'ambiguous' if len(decision_ids)>1 else 'covered' if accepted else 'uncovered' if complete else 'unknown'
                    result = board.copy(stack=False)
                    result.push_uci(move)
                    responses = reply.get('responses', [])
                    if (reply.get('status') != status or reply.get('decision_fen') != result.fen(en_passant='fen')
                            or len(responses) != len(accepted)
                            or {item.get('card_id'):item for item in responses} != accepted):
                        raise ValueError('response_scope_conflicts_with_accepted_routes')
        if seen != set(expected):
            raise ValueError('response_scope_inventory_incomplete')


MAX_STORED_SNAPSHOT_BYTES = 128 * 1024 * 1024


def snapshot_storage(document):
    raw=json.dumps(document,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if len(raw)>MAX_STORED_SNAPSHOT_BYTES:
        raise ValueError('snapshot_byte_bound')
    if len(raw)<=1024*1024:
        return document
    return {'_tempo_snapshot_storage':1,'sha256':hashlib.sha256(raw).hexdigest(),
            'data':base64.b64encode(zlib.compress(raw)).decode('ascii')}


def snapshot_document(value):
    if not isinstance(value,dict) or '_tempo_snapshot_storage' not in value:
        return value
    try:
        if type(value['_tempo_snapshot_storage']) is not int or value['_tempo_snapshot_storage']!=1 or len(value['data'])>2*MAX_STORED_SNAPSHOT_BYTES:
            raise ValueError()
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(value['data'],validate=True),MAX_STORED_SNAPSHOT_BYTES+1)
        if len(raw)>MAX_STORED_SNAPSHOT_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest()!=value['sha256']:
            raise ValueError()
        return json.loads(raw)
    except (KeyError,ValueError,TypeError,zlib.error):
        raise ValueError('snapshot_storage_corrupt') from None


def parse_snapshot(value):
    value = snapshot_document(value)
    if not isinstance(value, dict):
        return value
    version = value.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported_snapshot_version")
    return (ComparisonSnapshotV2 if version == 2 else ComparisonSnapshot).model_validate(value)
