"""Production planner/apply contracts used by Issue81 component and parity tests."""
from dataclasses import replace
import json
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend' / 'tests'))
from test_prefix_evaluation import line, snapshot, CARO_B, QGD
from test_prefix_transition import prepared_snapshot, changed_snapshot, card_row
from app.services.prefix_transition import plan_transition
from app.services.prefix_evaluation import evaluate_prefix, snapshot_identity
from app.prefix_transition_contracts import PrefixTransitionApplyRequest, PrefixTransitionApplicationResult


def transition_fixture():
    structural = runpy.run_path(str(Path(__file__).with_name('generate.py')))['comparison_fixture']()
    source = snapshot((line('a', depth=2), line('b', CARO_B), line('alias'), line('qgd', QGD)))
    prepared = prepared_snapshot(source)
    depths = {'a': 2, 'b': 2}
    ready = plan_transition(prepared, ('a', 'b'), depths)
    replacement = next(step for step in ready.proposed_steps if any(
        card.card_id == step.card_id and card.classification == 'new_replacement' for card in ready.cards))
    reused = changed_snapshot(prepared, 'cards', prepared.rows('cards') + [card_row(replacement, repertoire_id='other')])
    retired_id = next(card.card_id for card in ready.cards if card.classification == 'retired')
    shared = changed_snapshot(prepared, 'repertoire_cards', prepared.rows('repertoire_cards') + [
        dict(repertoire_id='other', card_id=retired_id, canonical_route_source=1)])
    blocked = changed_snapshot(prepared, 'repertoire_cards', [
        row | {'canonical_route_source': 1} if row['card_id'] == retired_id else row for row in prepared.rows('repertoire_cards')])
    fresh_source = replace(snapshot(tuple(replace(item, saved_depth=2) if item.id in depths else item for item in source.lines)), graph_generation=2)
    fresh_projection = structural['source'] | {
        'snapshot_id': snapshot_identity(fresh_source), 'graph_generation': 2,
        'lines': [dict(row, saved_depth=2) if row['id'] in depths else row for row in structural['source']['lines']],
        'current_depth_distribution': [{'depth': 2, 'line_count': 2}, {'depth': 3, 'line_count': 2}],
    }
    apply = PrefixTransitionApplyRequest(snapshot_id=ready.snapshot_id, selected_line_ids=ready.selected_line_ids,
        candidate_depths=depths, plan_id=ready.plan_id, transition_snapshot_id=ready.transition_snapshot_id,
        graph_generation=ready.graph_generation, study_day=ready.study_day)
    return dict(ready=ready.model_dump(mode='json'),
        reused=plan_transition(reused, ('a', 'b'), depths).model_dump(mode='json'),
        shared=plan_transition(shared, ('a', 'b'), depths).model_dump(mode='json'),
        blocked=plan_transition(blocked, ('a', 'b'), depths).model_dump(mode='json'),
        request=apply.model_dump(mode='json'),
        result=PrefixTransitionApplicationResult(status='complete', operation_id='operation', plan_id=ready.plan_id,
            repertoire_id='rep', graph_generation=2, no_op=False, queue_date=ready.study_day).model_dump(mode='json', exclude_none=True),
        fresh_source=fresh_projection, fresh_comparison=evaluate_prefix(fresh_source, ('a', 'b'), depths))


if __name__ == '__main__':
    print(json.dumps(transition_fixture(), indent=2))
