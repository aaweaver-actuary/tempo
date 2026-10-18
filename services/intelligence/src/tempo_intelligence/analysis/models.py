"""Analysis local wire 1. Source facts are explicit, never inferred from absence."""
from typing import Literal
from datetime import datetime
import chess
from pydantic import Field, model_validator, model_serializer
from ..models import StrictModel, Identifier, Revision, Digest, UciMove
from ..identity import digest

POLICY = "c-analysis-v1"

class Lineage(StrictModel):
    source_id: Identifier
    source_revision: Revision
    source_digest: str = Field(min_length=1, max_length=200)
    publication_id: Identifier | None = None
    publication_generation: Revision | None = None
    activation_id: Identifier | None = None
    activation_revision: Revision | None = None
    activation_status: Literal["activated", "unknown", "retired"] = "unknown"
    serving_status: Literal["current", "historical", "unknown"] = "unknown"

class Selection(StrictModel):
    @model_serializer(mode='wrap')
    def preserve_existing_bytes(self, handler):
        value=handler(self)
        if self.repertoire_title is None:
            value.pop('repertoire_title',None)
        return value

    repertoire_title: str | None = Field(default=None, max_length=2000)
    repertoire_id: Identifier
    policy_id: Identifier
    learner_color: Literal["white", "black"]
    lineage: Lineage

class ProposalAnchor(StrictModel):
    source_key: Identifier
    repertoire_key: Identifier
    policy_key: Identifier
    route_key: Identifier
    anchor_ply: int = Field(ge=0,le=999)

class CoverageScope(StrictModel):
    repertoire_id: Identifier
    policy_id: Identifier
    learner_color: Literal["white","black"]
    fen: str = Field(max_length=120)
    complete: bool
    revision: Revision
    covered_replies: list[UciMove] = Field(max_length=256)
    legal_moves: list[UciMove] = Field(max_length=256)
    evidence_id: Identifier

    @model_validator(mode="after")
    def legal_scope(self):
        board=chess.Board(self.fen)
        if not board.is_valid() or board.turn == (self.learner_color=="white") or set(self.legal_moves)!={m.uci() for m in board.legal_moves} or len(set(self.legal_moves))!=len(self.legal_moves) or not set(self.covered_replies)<=set(self.legal_moves):
            raise ValueError("invalid_complete_reply_inventory")
        return self

class Context(StrictModel):
    @model_serializer(mode='wrap')
    def preserve_prior_context_bytes(self,handler):
        value=handler(self)
        if self.learning_card_id is None:value.pop('learning_card_id',None)
        return value

    learning_card_id: Identifier | None = None
    ply: int = Field(ge=0, le=1000)
    repertoire_id: Identifier
    policy_id: Identifier
    status: Literal["matched", "missed", "unknown"] = "unknown"
    reason: str = Field(default="coverage_unknown", max_length=200)
    card_id: Identifier | None = None
    decision_id: Identifier | None = None
    expected_moves: list[UciMove] = Field(default_factory=list, max_length=256)
    route_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    coverage: Literal["covered", "uncovered", "unknown"] = "unknown"
    route_established: bool = False
    scope_complete: bool = False
    scope_revision: Revision | None = None
    opponent_ply: int | None = Field(default=None, ge=0, le=999)
    opponent_move: UciMove | None = None
    after_divergence: bool = False
    reentry_authorized: bool = False
    delivery_status: str = Field(default="unknown", max_length=200)
    delivery: dict | None = None
    evidence_id: Identifier
    proposal_anchor: ProposalAnchor | None = None

class OpponentEncounter(StrictModel):
    ply: int = Field(ge=0,le=999)
    repertoire_id: Identifier
    policy_id: Identifier
    fen: str = Field(max_length=120)
    route_ids: list[Identifier] = Field(min_length=1,max_length=10000)
    scope_complete: bool
    scope_revision: Revision
    evidence_id: Identifier


class Game(StrictModel):
    @model_serializer(mode='wrap')
    def preserve_existing_bytes(self, handler):
        value=handler(self)
        if self.game_url is None:
            value.pop('game_url',None)
        return value

    game_url: str | None = Field(default=None, max_length=2048)
    game_id: Identifier
    learner_id: Identifier | None = None
    provider: Identifier | None = None
    provider_game_id: Identifier | None = None
    source_revision: Revision
    source_digest: str = Field(min_length=1, max_length=200)
    learner_color: Literal["white", "black"]
    starting_fen: str = Field(max_length=120)
    moves: list[UciMove] = Field(min_length=1, max_length=1000)
    played_at: str | None = Field(default=None, max_length=80)
    opponent_encounters_complete: bool = False
    opponent_encounters: list[OpponentEncounter] = Field(default_factory=list,max_length=2000)
    contexts: list[Context] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def legal_game(self):
        if self.played_at is not None and datetime.fromisoformat(self.played_at.replace("Z","+00:00")).tzinfo is None:
            raise ValueError("game_chronology_requires_timezone")
        board = chess.Board(self.starting_fen)
        if not board.is_valid():
            raise ValueError("invalid_game_root")
        positions = []
        for move in self.moves:
            positions.append(board.copy())
            board.push_uci(move)
        for encounter in self.opponent_encounters:
            if encounter.ply>=len(positions) or positions[encounter.ply].turn==(self.learner_color=='white') or encounter.fen!=positions[encounter.ply].fen(en_passant='fen'):
                raise ValueError('opponent_encounter_history_mismatch')
        identities=[(item.ply,item.repertoire_id,item.policy_id) for item in self.opponent_encounters]
        if len(set(identities))!=len(identities):raise ValueError('duplicate_opponent_encounter')
        for context in self.contexts:
            if context.ply >= len(positions) or positions[context.ply].turn != (self.learner_color == "white"):
                raise ValueError("invalid_context_ply")
            if any(chess.Move.from_uci(move) not in positions[context.ply].legal_moves for move in context.expected_moves):
                raise ValueError("illegal_expected_move")
            if context.opponent_ply is not None and (context.opponent_ply != context.ply - 1 or self.moves[context.opponent_ply] != context.opponent_move):
                raise ValueError("opponent_context_mismatch")
        return self

class PageReference(StrictModel):
    page_id: Identifier
    digest: Digest
    game_count: int = Field(ge=0, le=64)

class Manifest(StrictModel):
    schema_version: Literal[1]
    workspace_id: Identifier
    input_id: Identifier
    revision: Revision
    producer: Literal["synthetic", "a-analysis-export"]
    producer_version: Identifier
    source_artifact_digest: Digest
    completeness: Literal["complete", "partial", "unavailable"]
    reasons: list[Identifier] = Field(default_factory=list, max_length=64)
    game_ids: list[Identifier] = Field(max_length=1000)
    window_start: str | None = Field(default=None, max_length=80)
    window_end: str | None = Field(default=None, max_length=80)
    selections: list[Selection] = Field(min_length=1, max_length=32)
    pages: list[PageReference] = Field(max_length=1024)
    coverage_scopes: list[CoverageScope] = Field(default_factory=list,max_length=2048)
    producer_manifest: dict | None = None

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.game_ids)) != len(self.game_ids) or len({p.page_id for p in self.pages}) != len(self.pages):
            raise ValueError("duplicate_input_identity")
        if self.producer=="a-analysis-export":
            original=self.producer_manifest
            if not original or original.get("kind")!="tempo-analysis-inputs" or type(original.get("schema_version")) is not int or original["schema_version"]!=1 or original.get("workspace_id")!=self.workspace_id or original.get("input_id")!=self.input_id or original.get("revision")!=self.revision:
                raise ValueError("A_manifest_identity_required")
            if digest({key:value for key,value in original.items() if key!="manifest_digest"})!=self.source_artifact_digest or self.source_artifact_digest!="sha256:"+original.get("manifest_digest",""):
                raise ValueError("A_manifest_digest_mismatch")
            for selection in self.selections:
                lineage=selection.lineage
                source,publication=original["source"],original["publication"]
                if (lineage.source_id,lineage.source_revision,lineage.source_digest,lineage.publication_id,lineage.publication_generation)!=(source["source_id"],source["source_revision"],source["source_digest"],publication["publication_id"],publication["publication_generation"]):
                    raise ValueError("A_source_lineage_mismatch")
                activation=original.get("activation") or {}
                if lineage.activation_status=="activated" and (activation.get("status"),activation.get("workspace_id"),activation.get("publication_id"),activation.get("publication_generation"),activation.get("activation_id"),activation.get("activation_revision"))!=("activated",self.workspace_id,lineage.publication_id,lineage.publication_generation,lineage.activation_id,lineage.activation_revision):
                    raise ValueError("A_actual_activation_receipt_required")
        if sum(p.game_count for p in self.pages) != len(self.game_ids):
            raise ValueError("input_game_count_mismatch")
        coverage_keys=[(s.repertoire_id,s.policy_id,s.learner_color," ".join(chess.Board(s.fen).fen(en_passant="legal").split()[:4])) for s in self.coverage_scopes]
        if len(set(coverage_keys))!=len(coverage_keys):
            raise ValueError("duplicate_coverage_scope")
        keys = [(s.repertoire_id, s.policy_id, s.learner_color) for s in self.selections]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate_selection")
        if self.completeness!="complete" and not self.reasons:
            raise ValueError("partial_input_reason_required")
        if self.completeness == "complete" and not self.game_ids:
            raise ValueError("empty_complete_scope")
        return self

class Page(StrictModel):
    schema_version: Literal[1]
    input_id: Identifier
    revision: Revision
    page_id: Identifier
    games: list[Game] = Field(max_length=64)

class Command(StrictModel):
    schema_version: Literal[1] = 1
    command_id: Identifier
    action: Literal["continue", "retry", "cancel", "resume", "dismiss", "reopen", "finalize"]
    expected_revision: Revision
    budget: int = Field(default=250, ge=1, le=10000)

class RunRequest(StrictModel):
    schema_version: Literal[1]
    workspace_id: Identifier
    run_id: Identifier
    revision: Revision
    expected_revision: Revision | None = None
    input_id: Identifier
    input_revision: Revision
    policy_version: Literal["c-analysis-v1"] = POLICY
    position_budget: int = Field(default=250, ge=0, le=10000)
    ranking_source: Literal["actual", "lichess", "masters", "maia"] = "actual"
    source_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1024)
    ranking_evidence_id: Identifier | None = None

    @model_validator(mode="after")
    def explicit_ranking_population(self):
        if self.ranking_source!="actual" and self.ranking_evidence_id not in self.source_evidence_ids:
            raise ValueError("explicit_ranking_evidence_required")
        return self


def payload_digest(value):
    return digest(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
