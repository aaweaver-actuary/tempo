"""Prepare complete exact-encounter source facts before the borrowed writer."""
import json
import time

from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute, DecisionPresentation
from app.study.introductions import encounter
from tempo_legacy_database.adapters.queue_refresh.application import admission as storage


def prepare_encounter_introduction(target: DecisionPresentation, *, read) -> encounter.PreparedEncounterIntroduction:
    identity = encounter.introduction_target_identity(target)
    def rejected(reason):
        return encounter.PreparedEncounterIntroduction(identity, '', (), reason)
    with read() as database:
        header, _, cards, graphs = storage.product_source_facts(
            database, target.repertoire_id, (target.presentation_id,), ())
    if (header is None or len(cards) != 1 or len(graphs) > 256
            or header['graph_state'] != 'ready'
            or header['scope_source_revision'] + 1 != target.repertoire_revision):
        return rejected('current_source_unproved')
    card = cards[0]
    expected = encounter.target_definition(target, card)
    if expected is None:
        return rejected('current_target_definition_unproved')
    def routes():
        after = ''
        while True:
            with read() as database:
                page = storage.product_target_route_page(database, target.repertoire_id, after)
            # No database/locks remain during model construction and chess work.
            for row in page:
                if row['oversized'] or not row['id']:
                    raise ValueError('target route exceeds source row bound')
                yield ImmutableAuthoredRoute(repertoire_id=target.repertoire_id, route_id=row['id'],
                    policy_id=target.repertoire_id, learner_color=row['trained_color'],
                    starting_fen=row['start_fen'], moves=tuple(json.loads(row['moves_json'])))
            if len(page) < 16:
                return
            after = page[-1]['id']
            time.sleep(0)  # Relinquish execution between closed, admitted read pages.
    alternatives = encounter.target_alternatives(routes(), expected=expected, learner_color=card['trained_color'])
    if alternatives.reason:
        return rejected(alternatives.reason)
    route_ids = tuple(sorted(route.route_id for route in alternatives.routes))
    with read() as database:
        current_header, lines, current_cards, current_graphs = storage.product_source_facts(
            database, target.repertoire_id, (target.presentation_id,), route_ids)
    if (current_header is None or len(current_cards) != 1
            or storage.introduction_source_binding(header, card, graphs, ()) !=
               storage.introduction_source_binding(current_header, current_cards[0], current_graphs, ())):
        return rejected('current_source_changed_or_busy')
    # Relevant rows must still be the exact routes that were completely scanned.
    route_rows = {route.route_id: (route.starting_fen, list(route.moves), route.learner_color)
                  for route in alternatives.routes}
    if (len(lines) != len(route_ids) or any(
        (line['start_fen'], json.loads(line['moves_json']), line['trained_color']) != route_rows.get(line['id'])
        for line in lines)):
        return rejected('current_source_changed_or_busy')
    from .queue_admission_adapter import _playable_introduction
    if not _playable_introduction(target, card, graphs, {line['id']: line for line in lines},
                                  learner_color=card['trained_color'], route_cache={}):
        return rejected('current_playable_context_unproved')
    return encounter.PreparedEncounterIntroduction(identity,
        storage.introduction_source_binding(current_header, current_cards[0], current_graphs, lines), route_ids)
