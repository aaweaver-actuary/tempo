"""Regular-suite contract proofs; fixtures contain synthetic identities only."""
import ast
import json
from importlib.util import resolve_name
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
sys.path.insert(0, str(ROOT / "packages/evidence-math/src"))
sys.path.insert(0, str(ROOT / "contracts/analysis-jobs/src"))
from app.contracts.product_v02.games import Finding, GamePage, GameSummary, ProviderSyncStatus, RecentGames
from app.contracts.product_v02.learning import GameEffectControl, GameLapse, LearningReceipt, GameEncounter, EncounterReceipt, withdrawal_only_delivery
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.contracts.product_v02.presentation import GameDetail
from app.contracts.product_v02.analysis import ProbabilityEvidence, EngineEvidence, Proposal, ProposalDecision, RankingInput, RankingOutput
from app.contracts.product_v02.presentation import Presentation, JoinPlan
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

FIXTURES = ROOT / "contracts/product-v02/fixtures"
MODELS = {model.__name__: model for model in (Finding, GamePage, RepertoireSnapshot, GameEffectControl, GameLapse, LearningReceipt, GameSummary, ProviderSyncStatus, RecentGames, GameDetail)}
MODELS.update({model.__name__: model for model in (ProbabilityEvidence, EngineEvidence, Proposal, ProposalDecision, RankingInput, RankingOutput, Presentation, JoinPlan)})


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["payload"]


def encounter_fixture(name):
    return json.loads((FIXTURES / "encounters" / f"{name}.json").read_text())["payload"]


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
import app.contracts.product_v02.presentation
import app.contracts.product_v02.analysis
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env={"PYTHONPATH": ":".join(map(str, (ROOT / "backend", ROOT / "packages/game-review-contracts/src", ROOT / "contracts/game-evidence/src", ROOT / "packages/chess-core/src", ROOT / "packages/evidence-math/src", ROOT / "contracts/analysis-jobs/src")))}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# Explicit new product roots only. Legacy modules outside these roots are not audited.
PRODUCT_ROOTS = {"games": ("backend/app/games",), "repertoire": ("backend/app/repertoire",), "study": ("backend/app/study/game_feedback.py", "backend/app/study/encounter_feedback.py", "backend/app/study/interfaces"), "contracts": ("backend/app/contracts/product_v02",)}


def import_targets(source, module):
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
            yield target
            yield from (f"{target}.{alias.name}" for alias in node.names)


def boundary_errors(sources):
    errors = []
    public_module = "app.study.interfaces.game_priority"
    public_tree = ast.parse((ROOT / "backend/app/study/interfaces/game_priority.py").read_text())
    public_exports = next(ast.literal_eval(node.value) for node in public_tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    for item in sources:
        owner, source = item[:2]
        module = item[2] if len(item) > 2 else f"app.{owner}.module"
        for name in import_targets(source, module):
            if owner == "games" and name in {"app.contracts.product_v02.learning.LearningFeedbackPort", "app.contracts.product_v02.learning.EncounterFeedbackPort"}:
                errors.append(f"{owner}: consume the designated Study priority interface {name}")
            # The supported Study surface contains only the unchanged pure
            # contracts. All other Study implementation imports stay private.
            if owner == "games" and (name == public_module or name.startswith(public_module + ".")):
                if name != public_module and name.removeprefix(public_module + ".") not in public_exports:
                    errors.append(f"{owner}: unavailable Study interface export {name}")
                continue
            if owner == "games" and name.startswith("app.") and not name.startswith(("app.games.", "app.contracts.", "app.chess.")):
                errors.append(f"{owner}: unsupported application dependency {name}")
                continue
            if name in {"app.main", "app.tasks"} or name.startswith(("app.main.", "app.tasks.")):
                errors.append(f"{owner}: domain imports composition {name}")
            for sibling in ("games", "repertoire", "study"):
                if sibling != owner and (name == f"app.{sibling}" or name.startswith(f"app.{sibling}.")):
                    errors.append(f"{owner}: private sibling import {name}")
        if owner == "games":
            tree = ast.parse(source)
            aliases = {alias.asname or alias.name: public_module for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names if alias.name == public_module}
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and aliases.get(ast.unparse(node.value), ast.unparse(node.value)) == public_module and node.attr not in public_exports:
                    errors.append(f"{owner}: unavailable Study interface member {node.attr}")
    return errors


def has_import_cycle(modules):
    edges = {name: set(import_targets(source, name)) & modules.keys() for name, source in modules.items()}
    visited, active = set(), set()
    def visit(name):
        if name in active: return True
        if name in visited: return False
        active.add(name)
        if any(visit(target) for target in edges[name]): return True
        active.remove(name)
        visited.add(name)
        return False
    return any(visit(name) for name in modules)


def test_new_product_boundaries_do_not_import_composition_or_private_siblings():
    sources = []
    modules = {}
    for owner, locations in PRODUCT_ROOTS.items():
        for location in locations:
            path = ROOT / location
            for file in ([path] if path.is_file() else path.rglob("*.py")):
                module = ".".join(file.relative_to(ROOT / "backend").with_suffix("").parts)
                sources.append((owner, file.read_text(), module))
                modules[module] = file.read_text()
    assert not boundary_errors(sources)
    assert not has_import_cycle(modules)
    assert boundary_errors([("study", "from app.games.feedback.comparison import compare")])
    assert boundary_errors([("games", "import app.main")])
    # Every cross-domain implementation edge is rejected, including both sides of a cycle.
    assert len(boundary_errors([("study", "import app.games.feedback"), ("games", "import app.study.game_feedback")])) == 2
    assert boundary_errors([("games", "from ...study.game_feedback import apply", "app.games.feedback.comparison")])
    assert boundary_errors([("games", "from app import main")])
    assert boundary_errors([("games", "from app.services.queue_application import seed_queue")])
    assert not boundary_errors([("games", "from app.study.interfaces.game_priority import EncounterFeedbackPort")])
    assert boundary_errors([("games", "from app.contracts.product_v02.learning import EncounterFeedbackPort")])
    assert boundary_errors([("games", "from app.study.interfaces.game_priority import PrivateStore")])
    assert boundary_errors([("games", "import app.study.interfaces.game_priority as api\ndef hidden():\n    return api.PrivateStore()")])
    assert has_import_cycle({"app.contracts.one": "from .two import value", "app.contracts.two": "from .one import value"})


def test_ui_history_status_and_filter_facts_remain_explicit():
    detail = GameDetail.model_validate_json(json.dumps(fixture("game-detail")))
    assert detail.moves == ("d2d4", "d7d5", "c2c4")
    assert detail.findings[0].played_uci == "d2d4"
    with pytest.raises(AttributeError): detail.moves.append("c7c6")
    failed = fixture("provider-sync-failed")
    assert ProviderSyncStatus.model_validate(failed).status == "failed"
    with pytest.raises(ValidationError): ProviderSyncStatus.model_validate(failed | {"error": None})
    assert GamePage.model_validate_json(json.dumps(fixture("incomplete-page"))).filters == (("rated", "false"), ("perfType", "rapid"))


@pytest.mark.parametrize("fault", ["negative", "renormalized", "personal_population", "illegal_count"])
def test_probability_evidence_preserves_unknowns_and_population(fault):
    payload = fixture("probability-unknown-mass")
    if fault == "negative": payload["distribution"]["moves"][0]["probability"] = -.1
    elif fault == "renormalized": payload["distribution"]["unknown_mass"] = 0
    elif fault == "personal_population": payload["basis"] = "personal"
    else: payload["counts"] = {"kind": "counts", "total_count": 1, "moves": [{"move_uci": "e7e5", "count": 1}]}
    with pytest.raises(ValidationError): ProbabilityEvidence.model_validate_json(json.dumps(payload))


def test_engine_requires_every_explicit_candidate_and_detaches_frozen_scores():
    payload = fixture("engine-explicit-candidate")
    value = EngineEvidence.model_validate_json(json.dumps(payload))
    assert len(value.requested_candidates) == 7  # Includes beyond an initial top five.
    assert value.candidates[-1].move_uci == "a2a3"
    assert value.best.score.value - value.candidates[-1].score.value == 24
    with pytest.raises(ValidationError): value.best.score.value = -999
    payload["candidates"].pop()
    with pytest.raises(ValidationError): EngineEvidence.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["source_revision", "explanation", "card_changes", "max_loss_cp"])
def test_proposal_approval_binds_all_displayed_content_and_source_revision(field):
    payload = fixture("proposal")
    proposal = Proposal.model_validate_json(json.dumps(payload))
    accept = ProposalDecision.model_validate_json(json.dumps(fixture("proposal-accept")))
    assert accept.matches_preview(proposal)
    if field == "source_revision": payload[field] = 2
    elif field == "explanation": payload[field] += " changed"
    elif field == "card_changes": payload[field][0]["moves"] = ["d2d4"]
    else:
        payload[field] = 300
        with pytest.raises(ValidationError): Proposal.model_validate_json(json.dumps(payload))
        payload["threshold_adjustment_reason"] = "explicit_context_change_requires_repreview"
    changed = Proposal.model_validate_json(json.dumps(payload))
    assert not accept.matches_preview(changed)


@pytest.mark.parametrize("fault", ["inherited_streak", "other_route", "lost_cue", "wrong_connection", "lost_lineage"])
def test_join_plan_preserves_exact_route_cues_lineage_and_zero_streak(fault):
    payload = fixture("join-a-b-to-ab")
    valid = JoinPlan.model_validate_json(json.dumps(payload))
    assert valid.result.clean_streak == 0 and valid.introduction_slots == 0
    assert valid.unresolved_obligation_ids == ("existing-game-obligation",)
    if fault == "inherited_streak": payload["result"]["clean_streak"] = 3
    elif fault == "other_route": payload["right"]["route_id"] = "other-route"
    elif fault == "lost_cue": payload["result"]["opponent_cues"] = []
    elif fault == "wrong_connection": payload["connecting_cues"][0]["position_key"] = "other-position"
    else: payload["result"]["lineage"] = []
    with pytest.raises(ValidationError): JoinPlan.model_validate_json(json.dumps(payload))



@pytest.mark.parametrize("path", sorted((FIXTURES / "encounters").glob("*.json")), ids=lambda path: path.stem)
def test_next_release_encounter_values_round_trip_without_changing_lapse_wire(path):
    document = json.loads(path.read_text())
    model = {"GameEncounter": GameEncounter, "EncounterReceipt": EncounterReceipt}[document["model"]]
    value = model.model_validate_json(json.dumps(document["payload"]))
    assert json.loads(value.model_dump_json()) == document["payload"]


@pytest.mark.parametrize("name", ["unintroduced-correct", "unintroduced-missed", "historical-missed"])
def test_encounter_facts_preserve_frequency_and_historical_importance_without_lapse(name):
    payload = encounter_fixture(name)
    value = GameEncounter.model_validate_json(json.dumps(payload))
    assert value.observation == ("matched" if value.played_uci == value.expected_uci else "missed")
    assert value.decision.introduced_before_game is False
    assert value.decision.introduction_game == value.source
    if name == "historical-missed":
        assert value.occurred_at < value.decision.introduced_at
        assert value.observation == "missed"  # Still importance, even when lapse-ineligible.
    original = fixture("lapse")
    original["finding"]["introduced_before_game"] = False
    with pytest.raises(ValidationError): GameLapse.model_validate_json(json.dumps(original))
    payload["decision"]["presentation_id"] = "changed-caller-input"
    assert value.decision.presentation_id == "existing-physical-card"
    with pytest.raises(ValidationError): value.source.source_revision = 2


@pytest.mark.parametrize("fault", ["key", "expected", "observation", "source_chronology"])
def test_encounter_rejects_incoherent_source_and_response_claims(fault):
    payload = encounter_fixture("unintroduced-missed")
    if fault == "key": payload["key"]["game_id"] = "different-game"
    elif fault == "expected": payload["expected_uci"] = "g1f3"
    elif fault == "observation": payload["observation"] = "matched"
    else: payload["decision"]["introduction_game"]["source_revision"] = 2
    with pytest.raises(ValidationError): GameEncounter.model_validate_json(json.dumps(payload))


def test_same_encounter_key_and_hold_survive_later_introduction_and_recomparison():
    payload = encounter_fixture("unintroduced-missed")
    original = GameEncounter.model_validate_json(json.dumps(payload))
    payload["decision"].update(introduction="introduced", introduced_at="2026-10-26T13:00:00Z")
    later = GameEncounter.model_validate_json(json.dumps(payload))
    assert original.key.logical_id() == later.key.logical_id()
    assert original.source == later.source and original.detected_at == later.detected_at
    assert later.decision.introduced_before_game is False  # Admission cannot turn it into a lapse.
    hold = GameEffectControl(key=original.key, source_revision=2, action="hold", reason="source_corrected")
    assert hold.withdrawal is None and hold.key == later.key


@pytest.mark.parametrize("fault", ["correct_lapse", "held_available", "deferred_available", "paused_deadline", "reasonless_pending", "miss_frequency_only"])
def test_encounter_receipt_reports_disposition_and_control_deferral_honestly(fault):
    payload = encounter_fixture("paused-introduction")
    if fault == "correct_lapse": payload.update(observation="matched", disposition="existing_lapse")
    elif fault == "held_available": payload.update(status="held", admission_status="available", deferred_reason=None)
    elif fault == "deferred_available": payload["admission_status"] = "available"
    elif fault == "paused_deadline": payload["available_by"] = "2026-10-28T12:00:00Z"
    elif fault == "reasonless_pending": payload.update(deferred_reason=None, reason=None)
    else: payload.update(disposition="frequency_only", admission_status="not_requested", deferred_reason=None)
    with pytest.raises(ValidationError): EncounterReceipt.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("ordinary_control", ["zero_limit", "introduction_allowance", "study_only"])
def test_encounter_first_introduction_cannot_defer_to_ordinary_controls(ordinary_control):
    payload = encounter_fixture("paused-introduction")
    payload.update(deferred_reason=ordinary_control, reason=ordinary_control)
    with pytest.raises(ValidationError, match="targeted exception"):
        EncounterReceipt.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("safety_guard", ["paused", "disabled", "active_attempt", "authority_unavailable", "unknown_chronology"])
def test_targeted_encounter_exception_retains_explicit_pause_and_safety_guards(safety_guard):
    payload = encounter_fixture("paused-introduction")
    payload.update(deferred_reason=safety_guard, reason=safety_guard)
    receipt = EncounterReceipt.model_validate_json(json.dumps(payload))
    assert receipt.admission_status == "pending" and receipt.available_by is None
    assert receipt.detected_at == payload["detected_at"] and receipt.observation_recorded
