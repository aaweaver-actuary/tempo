"""Detached preview tests; these do not qualify a migration or its locks."""
import json

import pytest

from app.repertoire import build_index
from tempo_evidence_math.position_learning import SourceCertificate
from tempo_legacy_database.adapters.authored_repertoires import position_study_source as owner
from tooling.position_study_inventory import InventoryRequest, SourceFacts
from tooling.position_study_seed import preview_seed_page
from test_position_source_authority import setup


def test_preview_uses_owner_prepared_source_and_retains_provenance(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    membership = prepared.snapshot.membership
    certificate = SourceCertificate(membership.snapshot_id, membership.source_revision,
                                    membership.digest, True, True)
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), certificate),), ())
    first = preview_seed_page(prepared, request, limit=1)
    assert first == preview_seed_page(prepared, request, limit=1)
    assert first.decisions and all(item.hold for item in first.decisions)
    assert json.loads(first.source_provenance_json) == prepared.authored_token.as_dict()
    assert first.authored_digest == prepared.authored_token.authored_digest
    assert first.authored_digest != membership.digest


def test_preview_refuses_unprepared_or_unbounded_requests(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), None),), ())
    with pytest.raises(ValueError, match="prepared_position_source_required"):
        preview_seed_page(object(), request)
    with pytest.raises(ValueError, match="invalid_page_limit"):
        preview_seed_page(prepared, request, limit=501)
    # Source-level holds remain explicit, even with a cursor.
    assert preview_seed_page(prepared, request, after=("standard", "z")).decisions[0].hold
