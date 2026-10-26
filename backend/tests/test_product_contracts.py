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
    assert value.membership.routes[0].moves == ("e2e4",)
    second = RepertoireSnapshot.model_validate({"membership": value.membership, "variant": value.variant, "learner_color": value.learner_color, "boundaries": value.boundaries, "decision_presentations": value.decision_presentations})
    with pytest.raises(AttributeError): value.membership.routes[0].moves.append("e7e5")
    with pytest.raises(ValidationError): value.membership.routes[0].moves = ("d2d4",)
    with pytest.raises(AttributeError): value.membership.scope_repertoire_ids.append("other")
    with pytest.raises(AttributeError): value.membership.routes.append(value.membership.routes[0])
    assert second.membership.routes[0].moves == ("e2e4",)


def test_hold_and_withdraw_reuse_stable_occurrence_and_existing_v3_wire():
    lapse = GameLapse.model_validate_json(json.dumps(fixture("lapse")))
    hold = GameEffectControl.model_validate_json(json.dumps(fixture("withdraw")))
    assert hold.key.logical_id() == lapse.key.logical_id()
    assert hold.source_revision > lapse.finding.source_revision
    original = EvidenceDelivery.model_validate(json.loads((ROOT / "contracts/game-evidence/fixtures/v3/delivery.json").read_text())["payload"])
    result = withdrawal_only_delivery(original, hold.withdrawal)
    assert not any((result.encounters, result.comparisons, result.misses, result.obligations))
    assert result.withdrawals[0].model_dump() == hold.withdrawal.model_dump()
    with pytest.raises(ValidationError): hold.withdrawal.reason = "reanalysis"
    assert result.withdrawals[0].reason == "source_corrected"
    assert original.obligations  # Adapter did not mutate supplied envelope.


def test_pre_admission_hold_can_precede_original_without_owner_identity():
    hold = GameEffectControl.model_validate_json(json.dumps(fixture("hold")))
    delayed_original = GameLapse.model_validate_json(json.dumps(fixture("lapse")))
    assert hold.withdrawal is None and hold.reason == "source_corrected"
    assert hold.key == delayed_original.key
    assert hold.source_revision > delayed_original.finding.source_revision
    # This sequence is representable before any admission/receipt exists. Study
    # owns durable tombstone/race proof; producers need no guessed obligation ID.
    assert [type(item).__name__ for item in (hold, delayed_original)] == ["GameEffectControl", "GameLapse"]
    missing_reason = fixture("hold") | {"reason": None}
    with pytest.raises(ValidationError): GameEffectControl.model_validate_json(json.dumps(missing_reason))


def test_current_availability_and_unchanged_introduced_content_are_expressible():
    payload = fixture("snapshot")
    valid = RepertoireSnapshot.model_validate_json(json.dumps(payload)).decision_presentations[0]
    assert valid.available and valid.introduction_matches_current_content
    del payload["decision_presentations"][0]["available"]
    del payload["decision_presentations"][0]["introduction_matches_current_content"]
    unknown = RepertoireSnapshot.model_validate_json(json.dumps(payload)).decision_presentations[0]
    assert not unknown.available and not unknown.introduction_matches_current_content


@pytest.mark.parametrize("before_game", [True, False])
def test_route_local_date_only_chronology_keeps_exact_game_source(before_game):
    payload = fixture("snapshot-route-game-introduction")
    payload["decision_presentations"][0]["introduced_before_game"] = before_game
    value = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    binding, = value.decision_presentations
    assert binding.route_id == "route-a" and binding.route_ply == 4
    assert len(value.membership.routes) == 2  # Same response after a legal transposition.
    assert binding.introduced_at is None  # Do not turn a supplied date into a timestamp.
    assert binding.introduced_before_game is before_game
    assert binding.introduction_game.game_id == "synthetic-transposed-game"
    with pytest.raises(ValidationError): binding.introduction_game.source_revision = 2
    changed = binding.introduction_game.model_copy(update={"source_revision": 2})
    assert changed != binding.introduction_game  # Other/corrected sources do not share the proof.


@pytest.mark.parametrize("field,value", [("route_id", None), ("route_ply", None), ("route_ply", 5), ("introduction_game", None), ("introduced_before_game", None)])
def test_partial_or_wrong_occurrence_and_chronology_scope_is_rejected(field, value):
    payload = fixture("snapshot-route-game-introduction")
    payload["decision_presentations"][0][field] = value
    with pytest.raises(ValidationError): RepertoireSnapshot.model_validate_json(json.dumps(payload))


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
