"""Production evaluator fixture shared by component and contract regressions."""
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend' / 'tests'))
from test_prefix_evaluation import snapshot, line, CARO_B, QGD
from app.services.prefix_evaluation import evaluate_prefix, snapshot_identity, ESTIMATE_BASIS


def comparison_fixture():
    source = snapshot((line('a', depth=2), line('b', CARO_B), line('alias'), line('qgd', QGD)))
    result = {'source': {
        'version': 1, 'preview_only': True, 'estimate_basis': ESTIMATE_BASIS,
        'repertoire_id': 'rep', 'graph_generation': 1, 'snapshot_id': snapshot_identity(source),
        'lines': [asdict(item) for item in source.lines],
        'current_depth_distribution': [{'depth': depth, 'line_count': count}
            for depth, count in sorted(Counter(item.saved_depth for item in source.lines).items())],
    }, 'comparisons': {}}
    for selection, depths in ((('a', 'b'), (1, 2, 3, 4)), (('a',), (2, 3)), (('qgd',), (2,))):
        for depth in depths:
            result['comparisons'][','.join(sorted(selection)) + ':' + str(depth)] = evaluate_prefix(
                source, selection, {identifier: depth for identifier in selection})
    return result


if __name__ == '__main__':
    print(json.dumps(comparison_fixture(), indent=2))
