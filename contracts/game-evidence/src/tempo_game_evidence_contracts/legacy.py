"""Observed legacy authority identity. Hashes exclude scheduling and review state."""
import hashlib
import json
from typing import Literal
from pydantic import Field
from ._wire import Identifier, Model


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decision_identity(card_id, offset, prefix):
    return "legacy-decision:" + fingerprint([card_id, offset, prefix])


class LegacyAuthorityToken(Model):
    kind: Literal["legacy_graph"]
    target_id: Identifier
    workspace_id: Identifier
    repertoire_id: Identifier
    scope_source_revision: int = Field(ge=0)
    canonical_prefix_revision: int = Field(ge=0)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalogue_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalogue_generation: int = Field(ge=0)
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_generation: int = Field(ge=1)
    graph_state: Literal["ready", "building", "failed"]

    def storage_identity(self):
        # Historical module columns are internal storage slots, not a claim of
        # Content publication. Only this explicitly typed variant uses the prefix.
        return "legacy-graph:" + fingerprint(self.model_dump(mode="json")), self.graph_generation


def authority_source_projection(tables):
    """Only source/executable authority, never mutable schedules or reviews."""
    return {
        "repertoires": sorted(({key: row[key] for key in (
            "id", "is_main", "canonical_prefix_revision", "canonical_prefix_moves_json",
            "canonical_prefix_preview_id", "scope_source_revision") if key in row}
            for row in tables["repertoires"]), key=canonical),
        "repertoire_lines": sorted(({"id": row["id"], "repertoire_id": row["repertoire_id"],
            "trained_color": row["trained_color"], "start_fen": row["start_fen"],
            "moves": json.loads(row["moves_json"])} for row in tables["repertoire_lines"]), key=canonical),
        "cards": sorted(({**{key: row[key] for key in (
            "id", "revision", "start_fen", "trained_color", "content_type", "archived",
            "pending_validation", "superseded_by", "repertoire_id", "canonical_route_source") if key in row},
            "moves": json.loads(row["moves_json"])}
            for row in tables["cards"]), key=canonical),
        "repertoire_cards": sorted(({key: row[key] for key in (
            "repertoire_id", "card_id", "canonical_route_source") if key in row}
            for row in tables["repertoire_cards"]), key=canonical),
    }


def authority_source_digest(tables):
    return fingerprint(authority_source_projection(tables))


def authority_graph_digest(graphs):
    return fingerprint(sorted(({key: row[key] for key in (
        "repertoire_id", "generation", "line_id", "decision_index", "card_id", "parent_card_id",
        "decision_fen_key", "starting_fen", "moves_json", "trained_color", "segment_kind",
        "first_decision_index", "last_decision_index", "decision_fen_keys_json") if key in row}
        for row in graphs), key=canonical))
