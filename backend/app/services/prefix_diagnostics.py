"""Bounded read projection of existing reducer output, never attempt reconstruction."""
from collections import Counter
from datetime import datetime
from ..prefix_diagnostics_contracts import PrefixDiagnosticsDetail

ATTEMPT_LIMIT = 100
RECENT_LIMIT = 20


def unassisted_response(observation: dict) -> bool:
    return (observation['first_response_uci'] is not None
            and not observation['assistance_before_response']
            and observation['disposition'] not in {'illegal', 'unverified'})


def project_prefix_diagnostics(manifest: dict, graph_generation: int,
                               attempts: list[dict], observations: list[dict]) -> dict:
    """Observations are PR #69's persisted facts, including its original study day."""
    selected_attempts = {attempt['attempt_id']: attempt for attempt in attempts[:ATTEMPT_LIMIT]}
    by_index: dict[int, list[dict]] = {decision['decision_index']: [] for decision in manifest['decisions']}
    for record in observations:
        attempt = selected_attempts.get(record['attempt_id'])
        if attempt is None:
            raise ValueError('Diagnostic observation is outside its bounded attempt window')
        observation = record['observation']
        index = observation['decision_index']
        if index not in by_index:
            raise ValueError('Diagnostic observation is outside its presentation')
        decision = manifest['decisions'][index]
        if (observation['decision_id'] != decision['decision_id']
                or observation['expected_uci'] != decision['expected_uci']):
            raise ValueError('Diagnostic observation differs from its presentation manifest')
        by_index[index].append({'attempt_id': record['attempt_id'], 'state': attempt['state'], **observation})
    decisions = []
    for decision in manifest['decisions']:
        reached = by_index[decision['decision_index']]
        responded = [observation for observation in reached if observation['first_response_uci'] is not None]
        unassisted = [observation for observation in reached if unassisted_response(observation)]
        response_days = {observation['study_day'] for observation in unassisted}
        clean_days = {observation['study_day'] for observation in reached if observation['clean']}
        categories = Counter(category for observation in reached for category in observation['assistance_before_response'])
        decisions.append({**decision,
            'coverage': 'unknown' if not unassisted else 'strong' if len(response_days) >= 3 else 'weak',
            'reached_observations': len(reached), 'first_responses': len(responded),
            'first_response_failures': sum(observation['first_response_correct'] is False for observation in responded),
            'unassisted_first_responses': len(unassisted),
            'unassisted_first_response_failures': sum(observation['first_response_correct'] is False for observation in unassisted),
            'clean_successes': sum(observation['clean'] for observation in reached),
            'assistance_before_response': sum(bool(observation['assistance_before_response']) for observation in reached),
            'assistance_categories': dict(categories),
            'manual_failures': sum(observation['manual_failure'] for observation in reached),
            'corrections': sum(observation['corrected'] for observation in reached),
            'reveals': sum(observation['revealed'] for observation in reached),
            'distinct_clean_days': len(clean_days), 'distinct_unassisted_response_days': len(response_days),
            'recent_outcomes': sorted(reached, key=lambda observation: (
                datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00')), observation['attempt_id'], observation['decision_index']), reverse=True)[:RECENT_LIMIT],
        })
    selected = attempts[:ATTEMPT_LIMIT]
    return PrefixDiagnosticsDetail(manifest=manifest, graph_generation=graph_generation, decisions=decisions,
        window={'attempt_count': len(selected), 'older_attempts_excluded': len(attempts) > ATTEMPT_LIMIT,
                'newest_started_at': str(selected[0]['started_at']) if selected else None,
                'oldest_started_at': str(selected[-1]['started_at']) if selected else None}).model_dump(mode='json')
