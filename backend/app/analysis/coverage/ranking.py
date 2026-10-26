"""Pure reach ranking. No provider, persistence, engine or Study calls."""
from __future__ import annotations

from app.contracts.product_v02.analysis import RankedDecision, RankingInput, RankingOutput

POLICY_VERSION = "population-reach-personal-tie-v1"


def rank_candidates(facts: RankingInput) -> RankingOutput:
    """Population reach first; personal games break ties without fusing estimates."""
    identities = [(item.repertoire_id, item.decision_id) for item in facts.candidates]
    if len(set(identities)) != len(identities):
        raise ValueError("ranking_requires_unique_scoped_decisions")
    ordered = sorted(facts.candidates, key=lambda item: (
        item.reach_probability is None, -(item.reach_probability or 0),
        -(item.personal_encounters or 0), item.uncertainty is None,
        item.uncertainty or 0, item.evidence_support is None,
        -(item.evidence_support or 0), item.repertoire_id, item.decision_id))
    return RankingOutput(based_on=facts, ordered=tuple(
        RankedDecision(decision_id=item.decision_id, rank=rank,
                       reason=POLICY_VERSION if item.reach_probability is not None else "population_reach_unknown")
        for rank, item in enumerate(ordered, 1)),
        unavailable_reason="population_reach_unknown" if ordered and all(
            item.reach_probability is None for item in ordered) else None)
