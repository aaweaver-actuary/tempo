"""Synthetic persisted frozen rank fixture; no inference or planner invoked."""
from app.study.openings.frozen_math import content_fingerprint, VERSION, READER_VERSION


def staged_plan(database, identifiers, *, run_id, provenance, deferred_id=None):
    ranks = []
    for rank, identifier in enumerate(identifiers, 1):
        card = dict(database.execute('SELECT * FROM cards WHERE id=?', (identifier,)).fetchone())
        memberships = [card['repertoire_id']]
        ranks.append({'id': identifier, 'rank': rank, 'memberships': memberships,
                      'fingerprint': content_fingerprint(card, memberships)})
    deferred = ([{'id': deferred_id, 'rank': len(ranks), 'memberships': ranks[-1]['memberships'],
                  'scoring_status': 'deferred', 'marginal_score': None}] if deferred_id else [])
    return {'run_id': run_id, 'checksum': run_id, 'version': VERSION,
            'required_reader_version': READER_VERSION, 'provenance': provenance, 'ranks': ranks,
            'activation_safe': True, 'scoring_policy': 'allow_unscored_tail',
            'scored_count': len(ranks) - len(deferred), 'deferred_count': len(deferred),
            'deferred_fraction': len(deferred) / len(ranks), 'lineage_diagnostic_count': len(deferred),
            'deferred_cards': deferred}
