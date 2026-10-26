"""Hand-computable synthetic policy facts; no live infrastructure."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in ("packages/game-review-contracts/src", "contracts/game-evidence/src",
             "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / path))

from app.contracts.product_v02.analysis import RankingCandidate, RankingInput
from app.analysis.coverage.ranking import rank_candidates


def facts(*candidates):
    return RankingInput(repertoire_revisions=(("rep", 1),), candidates=tuple(candidates), evidence_ids=())


def candidate(identity, reach, personal=0):
    return RankingCandidate(decision_id=identity, repertoire_id="rep", reach_probability=reach,
                            evidence_support=None, uncertainty=None, personal_encounters=personal)


def test_twenty_percent_precedes_two_percent():
    result = rank_candidates(facts(candidate("two", .02), candidate("twenty", .2)))
    assert [item.decision_id for item in result.ordered] == ["twenty", "two"]


def test_personal_tie_break_keeps_population_estimates_separate():
    supplied = facts(candidate("a", .2), candidate("b", .2, personal=1))
    result = rank_candidates(supplied)
    assert [item.decision_id for item in result.ordered] == ["b", "a"]
    assert result.based_on == supplied


def test_unknown_reach_is_unavailable_not_zero():
    result = rank_candidates(facts(candidate("unknown", None)))
    assert result.unavailable_reason == "population_reach_unknown"
    assert result.based_on.candidates[0].reach_probability is None
