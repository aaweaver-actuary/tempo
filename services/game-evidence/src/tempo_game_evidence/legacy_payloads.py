"""One-game v3 producer over the shared legal/route-aware comparison owner.

No backend/runtime imports or persistence. v2 payload construction is unchanged.
The first-slice file operator supports initial evidence, not inferred corrections.
"""
from tempo_game_evidence_contracts import v3
from tempo_game_evidence_contracts.legacy import fingerprint

from .comparison import compare_game
from .legacy_scoped_authority import LegacyScopedComparisonSnapshot
from .source import identity, replay_positions


def preview_legacy_observation(observation):
    if (observation.get('kind') != 'legacy_game_observation'
            or type(observation.get('schema_version')) is not int or observation['schema_version'] != 1):
        raise ValueError('one_legacy_game_observation_required')
    game = observation['game']
    if type(game.get('revision')) is not int or game['revision'] != 1:
        raise ValueError('legacy_initial_evidence_only')
    snapshot = LegacyScopedComparisonSnapshot.model_validate(observation['snapshot'])
    source = v3.LegacyGameSource.model_validate(observation['game_source'])
    if (source.content_hash != snapshot.catalogue_scope.scope.content_hash
            or source.raw_source_digest != game['source_digest']):
        raise ValueError('legacy_game_source_changed')
    findings = compare_game(game, replay_positions(game), snapshot)
    misses = [finding for finding in findings if finding['status'] == 'missed']
    delivery, reason = None, None
    if len({finding['card_id'] for finding in misses}) > 1:
        result, reason = 'unsupported', 'one_physical_card_scope_required'
    elif misses:
        delivery = _delivery(game, snapshot, source, misses).model_dump(mode='json')
        result = 'miss_obligation'
    else:
        result = 'unknown' if all(finding['status'] == 'unknown' for finding in findings) else 'no_miss_obligation'
    document = dict(kind='legacy_game_preview', schema_version=1, result=result, reason=reason,
        target_id=snapshot.authority.target_id, workspace_id=snapshot.authority.workspace_id,
        repertoire_id=snapshot.authority.repertoire_id, game_id=game['game_id'],
        source_content_hash=source.content_hash, captured_at=snapshot.captured_at,
        findings=findings, delivery=delivery)
    return {**document, 'preview_digest': fingerprint(document)}


def _delivery(game, snapshot, source, misses):
    evidence_id = identity('legacy-evidence', game['workspace_id'], game['game_id'], snapshot.authority.repertoire_id)
    revision = 1
    comparisons, obligations, encounters, real_misses = [], [], [], []
    cards = {card.card_id: card for card in snapshot.cards}
    for finding in misses:
        card = cards[finding['card_id']]
        members = {(item['repertoire_id'], item['route_id']) for item in finding['memberships']}
        bindings = sorted((route.route_id, binding.ply, binding.exercise_ply)
            for route in snapshot.routes if (route.repertoire_id, route.route_id) in members
            for binding in route.bindings if binding.card_id == card.card_id and binding.ply == finding['ply']
            and (binding.decision_id or card.decision_id) == finding['decision_id'])
        if not bindings or len({item[2] for item in bindings}) != 1:
            raise ValueError('legacy_miss_requires_exact_physical_decision_binding')
        route_id, route_ply, exercise_ply = bindings[0]
        encounter_id = finding['encounter_id']
        miss_id = identity('legacy-miss', encounter_id)
        encounters.append(dict(encounter_id=encounter_id, ply=finding['ply'],
            position_key=finding.get('decision_position_key', finding['position_key']),
            decision_id=finding['decision_id'], observed_move=finding['move']))
        comparisons.append(dict(encounter_id=encounter_id, card_id=card.card_id, card_revision=card.card_revision,
            decision_id=finding['decision_id'], expected_moves=finding['expected_moves'], status='missed',
            route_id=route_id, route_ply=route_ply, exercise_ply=exercise_ply,
            executable_digest=finding['executable_digest']))
        real_misses.append(dict(miss_id=miss_id, encounter_id=encounter_id,
            decision_id=finding['decision_id'], reason=finding['explanation']))
        obligations.append(dict(obligation_id=identity('legacy-miss-obligation', evidence_id, encounter_id, card.card_id),
            obligation_revision=revision, source_evidence_id=evidence_id, source_revision=revision,
            card_id=card.card_id, card_revision=card.card_revision, decision_id=finding['decision_id'],
            encounter_id=encounter_id, miss_id=miss_id, kind='real_game_miss'))
    return v3.EvidenceDelivery(contract_version=3, workspace_id=game['workspace_id'],
        delivery_id=identity('legacy-delivery', evidence_id, revision, snapshot.capture_digest),
        evidence_id=evidence_id, evidence_revision=revision, game_id=game['game_id'], game_revision=game['revision'],
        occurred_at=game['played_at'], authority=snapshot.authority, capture_digest=snapshot.capture_digest,
        captured_at=snapshot.captured_at, game_source=source, encounters=encounters, comparisons=comparisons,
        misses=real_misses, obligations=obligations, withdrawals=[])
