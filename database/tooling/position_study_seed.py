"""Detached shadow-seed preview; it does not authorize or persist a seed.

A writer must fence the prepared source and current physical ownership, reserve
an FSRS identity across both stores, and atomically record the seed and its
checkpoint. Those owner capabilities are not yet provided by schema 57.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from tempo_legacy_database.adapters.authored_repertoires.position_study_source import PreparedPositionSource
from tooling.position_study_donors import DonorDecision, select_donors
from tooling.position_study_inventory import InventoryRequest


@dataclass(frozen=True)
class SeedPreviewPage:
    decisions: tuple[DonorDecision, ...]
    next_after: tuple[str, str] | None
    source_provenance_json: str
    authored_digest: str


def preview_seed_page(prepared: PreparedPositionSource, request: InventoryRequest, *,
                      after: tuple[str, str] | None = None, limit: int = 100) -> SeedPreviewPage:
    """Preview a bounded page of deterministic decisions for one owner source.

    The cursor is a preview cursor, not a durable completion receipt. All
    inputs must be refreshed and fenced at the eventual writer boundary.
    """
    if not isinstance(prepared, PreparedPositionSource):
        raise ValueError("prepared_position_source_required")
    if not isinstance(request, InventoryRequest) or len(request.sources) != 1:
        raise ValueError("one_inventory_source_required")
    if request.sources[0].snapshot != prepared.snapshot:
        raise ValueError("inventory_source_mismatch")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid_page_limit")
    if after is not None and (not isinstance(after, tuple) or len(after) != 2
                              or any(not isinstance(v, str) or not v for v in after)):
        raise ValueError("invalid_page_cursor")
    token = prepared.authored_token
    definitions = {(d.definition.key.repertoire_id, d.definition.key.variant,
                    d.definition.key.position, d.definition.expected_uci)
                   for route in prepared.routes for d in route.decisions}
    decisions = select_donors(request)
    rows = []
    for decision in decisions:
        selected, hold = decision.selected, decision.hold
        if selected is None:
            if hold.repertoire_id != token.repertoire_id:
                raise ValueError("inventory_repertoire_mismatch")
            if hold.variant is None or hold.position is None:
                # A repertoire-wide hold has no valid position cursor and must
                # remain visible rather than becoming an empty successful page.
                return SeedPreviewPage((decision,), None,
                    json.dumps(token.as_dict(), sort_keys=True, separators=(",", ":")), token.authored_digest)
            key = (hold.variant, hold.position)
        else:
            if (selected.repertoire_id, selected.variant, selected.position,
                    selected.selected_response_uci) not in definitions:
                raise ValueError("donor_position_not_in_prepared_source")
            key = (selected.variant, selected.position)
        rows.append((key, decision))
    rows.sort(key=lambda item: item[0])
    if len({key for key, _ in rows}) != len(rows):
        raise ValueError("duplicate_position")
    remaining = [item for item in rows if after is None or item[0] > after]
    page = remaining[:limit]
    cursor = page[-1][0] if len(remaining) > limit else None
    return SeedPreviewPage(tuple(item[1] for item in page), cursor,
        json.dumps(token.as_dict(), sort_keys=True, separators=(",", ":")), token.authored_digest)
