"""Regular-suite contract proofs; fixtures contain synthetic identities only."""
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
# Source-only shared values: no package installation/runtime composition needed.
sys.path.insert(0, str(ROOT / "packages/game-review-contracts/src"))
sys.path.insert(0, str(ROOT / "contracts/game-evidence/src"))
sys.path.insert(0, str(ROOT / "packages/chess-core/src"))
from app.contracts.product_v02.games import Finding, GamePage
from app.contracts.product_v02.learning import GameEffectControl, GameLapse, LearningReceipt, withdrawal_only_delivery
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

FIXTURES = ROOT / "contracts/product-v02/fixtures"
MODELS = {model.__name__: model for model in (Finding, GamePage, RepertoireSnapshot, GameEffectControl, GameLapse, LearningReceipt)}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["payload"]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_shared_json_round_trip(path):
    document = json.loads(path.read_text())
    model = MODELS[document["model"]]
    value = model.model_validate_json(json.dumps(document["payload"]))
    assert json.loads(value.model_dump_json()) == document["payload"]


@pytest.mark.parametrize("field,value", [("kind", "guessed_miss"), ("ply", 0), ("ply", True), ("source_revision", 0), ("introduced_before_game", None), ("expected_uci", ["e2e4", "g1f3"])])
def test_invalid_or_unproved_finding_cannot_authorize_lapse(field, value):
    payload = fixture("confirmed-introduced-miss")
    payload[field] = value
    with pytest.raises(ValidationError):
        Finding.model_validate_json(json.dumps(payload))


def test_saturated_page_and_conflicting_revisions_fail_closed():
    payload = fixture("incomplete-page")
    payload["complete"] = True
    with pytest.raises(ValidationError): GamePage.model_validate_json(json.dumps(payload))
    payload["complete"] = False
    payload["records"].append(payload["records"][0] | {"source_revision": 2})
    with pytest.raises(ValidationError): GamePage.model_validate_json(json.dumps(payload))


def test_snapshot_revision_conflict_and_caller_alias_are_rejected():
    payload = fixture("snapshot")
    payload["decision_presentations"][0]["repertoire_revision"] = 2
    with pytest.raises(ValidationError): RepertoireSnapshot.model_validate_json(json.dumps(payload))
    payload = fixture("snapshot")
    value = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    payload["membership"]["routes"][0]["moves"].append("e7e5")
    assert value.membership.routes[0].moves == ["e2e4"]
    second = RepertoireSnapshot.model_validate({"membership": value.membership, "variant": value.variant, "learner_color": value.learner_color, "boundaries": value.boundaries, "decision_presentations": value.decision_presentations})
    value.membership.routes[0].moves.append("e7e5")
    assert second.membership.routes[0].moves == ["e2e4"]


def test_hold_and_withdraw_reuse_stable_occurrence_and_existing_v3_wire():
    lapse = GameLapse.model_validate_json(json.dumps(fixture("lapse")))
    hold = GameEffectControl.model_validate_json(json.dumps(fixture("hold")))
    assert hold.key.logical_id() == lapse.key.logical_id()
    assert hold.source_revision > lapse.finding.source_revision
    original = EvidenceDelivery.model_validate(json.loads((ROOT / "contracts/game-evidence/fixtures/v3/delivery.json").read_text())["payload"])
    result = withdrawal_only_delivery(original, hold.withdrawal)
    assert not any((result.encounters, result.comparisons, result.misses, result.obligations))
    assert result.withdrawals == [hold.withdrawal]
    hold.withdrawal.reason = "reanalysis"
    assert result.withdrawals[0].reason == "source_corrected"
    assert original.obligations  # Adapter did not mutate supplied envelope.


def test_contract_imports_have_no_runtime_side_effects():
    script = '''
import builtins, sys
denied = {"requests", "httpx", "psycopg", "sqlite3", "fastapi", "app.main", "app.tasks", "subprocess"}
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name in denied: raise AssertionError("runtime import: " + name)
    return original(name, *args, **kwargs)
import pydantic
import tempo_game_review_contracts
import tempo_game_evidence_contracts.v3
builtins.__import__ = checked
import app.contracts.product_v02.games
import app.contracts.product_v02.repertoire
import app.contracts.product_v02.learning
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env={"PYTHONPATH": ":".join(map(str, (ROOT / "backend", ROOT / "packages/game-review-contracts/src", ROOT / "contracts/game-evidence/src", ROOT / "packages/chess-core/src")))}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# Explicit new product roots only. Legacy modules outside these roots are not audited.
PRODUCT_ROOTS = {"games": ("backend/app/games/feedback",), "repertoire": ("backend/app/repertoire",), "study": ("backend/app/study/game_feedback.py",), "contracts": ("backend/app/contracts/product_v02",)}


def boundary_errors(sources):
    errors = []
    for owner, source in sources:
        for node in ast.walk(ast.parse(source)):
            imports = [node.module or ""] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            for name in imports:
                if name in {"app.main", "app.tasks"} or name.startswith(("app.main.", "app.tasks.")):
                    errors.append(f"{owner}: domain imports composition {name}")
                for sibling in ("games", "repertoire", "study"):
                    if sibling != owner and (name == f"app.{sibling}" or name.startswith(f"app.{sibling}.")):
                        errors.append(f"{owner}: private sibling import {name}")
    return errors


def test_new_product_boundaries_do_not_import_composition_or_private_siblings():
    sources = []
    for owner, locations in PRODUCT_ROOTS.items():
        for location in locations:
            path = ROOT / location
            for file in ([path] if path.is_file() else path.rglob("*.py")):
                sources.append((owner, file.read_text()))
    assert not boundary_errors(sources)
    assert boundary_errors([("study", "from app.games.feedback.comparison import compare")])
    assert boundary_errors([("games", "import app.main")])
    # Every cross-domain implementation edge is rejected, including both sides of a cycle.
    assert len(boundary_errors([("study", "import app.games.feedback"), ("games", "import app.study.game_feedback")])) == 2
