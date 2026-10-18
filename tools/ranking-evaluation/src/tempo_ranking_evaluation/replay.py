"""Score replay and actual mathematics are deliberately separate operations."""
import json
from tempo_evidence_math import (load_snapshot, PrecomputedValuationScorer,
    load_valuation_snapshot, evaluate_opening_card_valuation, valuation_snapshot_document)
from .ranking import rank, compare


def replay_opening_card_valuation(document,top_k=(1,5,10)):
    candidates,context=load_snapshot(document)
    ranking=rank(candidates,context,PrecomputedValuationScorer())
    result=compare(candidates,(ranking,),top_k)
    result.update(snapshot_id=context.snapshot_id,as_of=context.as_of,study_day=context.study_day,
        source_versions=json.loads(context.source_versions_json),
        scorer_configuration=json.loads(context.scorer_configuration_json),
        production_order_basis=document['production_order_basis'],
        historical_counterfactual_supported=False,limitations=document['limitations'],
        replay_basis='computed_score_replay_not_recalculation')
    return result


def score_snapshot(document,top_k=(1,5,10)):
    result=evaluate_opening_card_valuation(load_valuation_snapshot(document))
    snapshot=valuation_snapshot_document(result)
    comparison=replay_opening_card_valuation(snapshot,top_k)
    comparison.update(snapshot=snapshot,replay_basis='explicit_mathematical_snapshot_calculation')
    return comparison
