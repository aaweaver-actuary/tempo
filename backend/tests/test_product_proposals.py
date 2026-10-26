"""Synthetic fake-capability proof for #499; no content or learning effects."""
import json
from pathlib import Path
import sys

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/chess-core/src", "packages/evidence-math/src",
               "packages/game-review-contracts/src", "contracts/game-evidence/src",
               "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / source))

from app.contracts.product_v02.analysis import EngineEvidence
from app.analysis.proposals.acceptability import QualityPolicy, assess


def engine_fixture():
    return json.loads((ROOT / "contracts/product-v02/fixtures/engine-explicit-candidate.json").read_text())["payload"]


@pytest.mark.parametrize("color", ["white", "black"])
@pytest.mark.parametrize("loss,expected", [(30, "acceptable"), (31, "rejected")])
def test_30_cp_accepted_31_rejected_both_colors(color, loss, expected):
    payload = engine_fixture()
    if color == "black":
        board = chess.Board()
        board.push_uci("e2e4")
        payload["context"].update(learner_color="black", history_uci=["e2e4"], target_fen=board.fen())
        payload["best"]["move_uci"] = "e7e5"
        payload["requested_candidates"] = ["a7a6"]
        payload["candidates"] = [payload["candidates"][-1] | {"move_uci": "a7a6"}]
    payload["candidates"][-1]["score"]["value"] = 50 - loss
    evidence = EngineEvidence.model_validate_json(json.dumps(payload))
    result = assess(evidence.context, evidence, evidence.candidates[-1].move_uci)
    assert result.status == expected and result.loss_cp == loss


@pytest.mark.parametrize("change", ["mate", "context", "depth", "bounded"])
def test_inconclusive_mate_or_mismatched_settings_not_cp_acceptance(change):
    payload = engine_fixture()
    if change == "mate": payload["candidates"][-1]["score"] = {"kind": "mate", "value": 3}
    if change == "depth": payload["candidates"][-1]["depth"] = 11
    if change == "bounded": payload["candidates"][-1].update(score=None, unavailable_reason="lowerbound")
    evidence = EngineEvidence.model_validate_json(json.dumps(payload))
    context = evidence.context if change != "context" else evidence.context.model_copy(update={"hash_mb": 32})
    assert assess(context, evidence, "a2a3").status == "inconclusive"


def test_threshold_override_is_explicit():
    with pytest.raises(ValueError): QualityPolicy(max_loss_cp=31)
    assert QualityPolicy(max_loss_cp=31, adjustment_reason="explicit displayed research scenario").max_loss_cp == 31
