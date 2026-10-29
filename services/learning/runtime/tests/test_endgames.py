"""Retirement rejects old execution facts while ordinary native Study keeps working."""
from pathlib import Path
import runpy
import pytest
from pydantic import ValidationError
from tempo_learning_runtime.publications import PublicationBundle
from test_runtime import activate, bundle, head, review_payload, submit


def test_retired_endgame_publications_cannot_create_new_cards():
    payload = bundle()
    payload["facts"][0]["content_type"] = "endgame"
    with pytest.raises(ValidationError):
        PublicationBundle.model_validate(payload)


def test_retired_endgame_routes_leave_ordinary_review_usable(case):
    activate(case)
    card = head(case)
    for method, path in [("GET", "/api/endgames/templates"), ("POST", "/api/endgames/probe"), ("POST", "/api/endgames/templates/old/attempt")]:
        response = case.client.request(method, path, json={})
        assert response.status_code == 404, response.text
    response = submit(case, card, review_payload(card))
    assert response.status_code == 200, response.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


@pytest.mark.parametrize("owner", ["learning", "legacy"])
@pytest.mark.parametrize("conflict", [False, True], ids=["upgrade", "ownership-rollback"])
def test_native_endgames_retirement_preserves_other_families_and_fences_recreation(case, owner, conflict):
    root = Path(__file__).resolve().parents[4]
    proof = runpy.run_path(str(root / "database/tests/endgames_retirement.py"))
    proof["assert_retirement"](case.admin, owner, conflict)
