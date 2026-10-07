"""Pure shorter-prefix plans over immutable authoritative inputs.

This module never opens a connection or calls a mutation/publication handler.
"""
from collections import defaultdict
from dataclasses import dataclass
import json
from typing import Iterator

from ..prefix_transition_contracts import (
    AttemptDisposition, Blocker, CardDisposition, DepthChange, MembershipDisposition,
    PrefixTransitionPlan, SubmissionDisposition,
)
from .opening_graph import GraphStep
from .opening_segmentation import POLICY_VERSION, POSITION_VERSION, stable_key
from .prefix_evaluation import EVALUATION_VERSION, EvaluationSnapshot, PrefixEvaluationError, snapshot_identity, validate_source, evaluate_prefix
from .review_reconciliation import SCHEDULE_COLUMNS

TRANSITION_VERSION = 1


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


@dataclass(frozen=True)
class SnapshotTable:
    name: str
    rows: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, 'rows', tuple(sorted(self.rows)))


@dataclass(frozen=True)
class TransitionSnapshot:
    source: EvaluationSnapshot
    study_day: str
    tables: tuple[SnapshotTable, ...]
    lookup_card_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, 'tables', tuple(sorted(self.tables, key=lambda table: table.name)))
        object.__setattr__(self, 'lookup_card_ids', tuple(sorted(set(self.lookup_card_ids))))

    def rows(self, name):
        return [json.loads(row) for table in self.tables if table.name == name for row in table.rows]


def transition_snapshot_identity(snapshot: TransitionSnapshot) -> str:
    return stable_key('prefix-transition-snapshot', TRANSITION_VERSION,
                      snapshot_identity(snapshot.source), snapshot.study_day,
                      snapshot.lookup_card_ids, [(table.name, table.rows) for table in snapshot.tables])


def validate_plan_freshness(plan: PrefixTransitionPlan, snapshot: TransitionSnapshot) -> None:
    expected_plan_id = stable_key('prefix-transition-plan', TRANSITION_VERSION, plan.model_dump(mode='json', exclude={'plan_id'}))
    if (plan.version != TRANSITION_VERSION or plan.repertoire_id != snapshot.source.repertoire_id
            or plan.transition_snapshot_id != transition_snapshot_identity(snapshot) or plan.plan_id != expected_plan_id):
        raise PrefixEvaluationError('stale_plan', 'Transition inputs changed. Refresh the source and build a new plan.')


def validate_shortening(source, selected_line_ids, candidate_depths):
    validate_source(source)
    selected = set(selected_line_ids)
    saved_depths = {line.id: line.saved_depth for line in source.lines}
    if (len(selected) != len(selected_line_ids) or not selected <= saved_depths.keys()
            or set(candidate_depths) != selected):
        raise PrefixEvaluationError('invalid_selection', 'Provide unique source line IDs and exactly their proposed depths.')
    for line_id, proposed_depth in candidate_depths.items():
        if type(proposed_depth) is not int or not 1 <= proposed_depth <= 20:
            raise PrefixEvaluationError('invalid_selection', 'Proposed depths must be integers from 1 to 20.')
        if proposed_depth > saved_depths[line_id]:
            raise PrefixEvaluationError('prefix_lengthening', f'Line {line_id} cannot lengthen. Choose a depth at most {saved_depths[line_id]}.')


def evaluation_steps(presentation):
    return tuple(GraphStep(**{**step, 'moves': tuple(step['moves']),
                              'decision_fen_keys': tuple(step['decision_fen_keys'])})
                 for step in presentation['steps'])


def _payload(card):
    return (' '.join(card['start_fen'].split()[:4]), tuple(json.loads(card['moves_json'])), card['trained_color'])


def _route_decisions(steps, presentation):
    decisions_by_card = {card['card_id']: tuple(card['decision_ids']) for card in presentation['cards']}
    by_line = defaultdict(list)
    for step in steps:
        by_line[step.line_id].extend(decisions_by_card[step.card_id])
    return dict(by_line)


def iter_transition_plan(snapshot: TransitionSnapshot, selected_line_ids, candidate_depths,
                         evaluation) -> Iterator[None]:
    """Yield between classifications; HTTP owns deadline/admission and source parity."""
    source = snapshot.source
    validate_shortening(source, selected_line_ids, candidate_depths)
    if evaluation['snapshot_id'] != snapshot_identity(source):
        raise PrefixEvaluationError('stale_snapshot', 'Structural calculation no longer matches the captured source.')
    current_steps = evaluation_steps(evaluation['whole_repertoire']['current'])
    proposed_steps = evaluation_steps(evaluation['whole_repertoire']['proposed'])
    selected = set(selected_line_ids)
    requested = {item['line_id']: item['requested_depth'] for item in evaluation['line_depths']}
    if (requested != candidate_depths or set(evaluation['selected_line_ids']) != selected
            or current_steps != tuple(sorted(source.published_steps, key=lambda step: (step.line_id, step.decision_index)))):
        raise PrefixEvaluationError('unsupported_source', 'Calculation does not match the selected transition. Recalculate from the authoritative source.')
    if (_route_decisions(current_steps, evaluation['whole_repertoire']['current'])
            != _route_decisions(proposed_steps, evaluation['whole_repertoire']['proposed'])
            or tuple(step for step in current_steps if step.line_id not in selected)
            != tuple(step for step in proposed_steps if step.line_id not in selected)):
        raise PrefixEvaluationError('coverage_conflict', 'Candidate does not preserve every source route and learner decision. Repair the source or splits.')
    depth_changes = tuple(DepthChange(line_id=line.id, before=line.saved_depth, after=candidate_depths[line.id])
                          for line in sorted(source.lines, key=lambda line: line.id)
                          if line.id in selected and candidate_depths[line.id] != line.saved_depth)
    cards_by_id = {card['id']: card for card in snapshot.rows('cards')}
    current_ids = {step.card_id for step in current_steps}
    proposed_by_id = defaultdict(list)
    for step in proposed_steps:
        proposed_by_id[step.card_id].append(step)
    links_by_card = defaultdict(list)
    for membership in snapshot.rows('repertoire_cards'):
        links_by_card[membership['card_id']].append(membership)
    target_links = {link['card_id'] for links in links_by_card.values() for link in links
                    if link['repertoire_id'] == source.repertoire_id}
    affected_ids = current_ids | proposed_by_id.keys() | target_links
    if not affected_ids <= set(snapshot.lookup_card_ids):
        raise PrefixEvaluationError('unsupported_source', 'Transition snapshot omitted a required card lookup. Recapture the complete snapshot.')
    seeds_by_card = {seed['card_id']: seed for seed in snapshot.rows('opening_card_schedule_seeds')}
    reviews_by_card = defaultdict(list)
    for review in snapshot.rows('reviews'):
        reviews_by_card[review['card_id']].append(review['id'])
    other_steps_by_card = defaultdict(list)
    for graph_step in snapshot.rows('other_graph_steps'):
        other_steps_by_card[graph_step['card_id']].append(graph_step)
    card_plans, membership_plans, blockers = [], [], []
    retired_ids, conflicting_ids = set(), set()

    def block(code, card_id, reason):
        blockers.append(Blocker(code=code, object_id=card_id, reason=reason))
        conflicting_ids.add(card_id)

    for override in source.prefix_overrides:
        children = {override.shortened_card_id, override.continuation_card_id}
        if depth_changes and children & current_ids and not children <= proposed_by_id.keys():
            for child_id in sorted(children & current_ids):
                block('saved_split_conflict', child_id,
                      f'Shortening bypasses saved split {override.source_card_id}. Resolve that saved split explicitly before changing its route depth.')
    for integrity_block in snapshot.rows('repertoire_integrity_card_blocks'):
        if integrity_block['card_id'] in affected_ids:
            block('integrity_conflict', integrity_block['card_id'],
                  f"Resolve integrity issue {integrity_block['issue_id']} in repertoire {integrity_block['repertoire_id']} before shortening.")
    for publication in snapshot.rows('publications'):
        if (publication['state'] != 'ready' or publication.get('task_generation', 0) is not None
                and (publication.get('task_generation', 0) > publication['generation']
                     or publication.get('task_state') not in {None, 'complete'})):
            for card_id, links in links_by_card.items():
                if card_id in affected_ids and any(link['repertoire_id'] == publication['repertoire_id'] for link in links):
                    block('shared_publication_busy', card_id, 'A referenced repertoire graph is changing. Wait for publication and refresh the plan.')

    for card_id in sorted(affected_ids):
        card = cards_by_id.get(card_id)
        links = sorted(links_by_card[card_id], key=lambda link: link['repertoire_id'])
        own_link = next((link for link in links if link['repertoire_id'] == source.repertoire_id), None)
        retained_links = [link for link in links if link['repertoire_id'] != source.repertoire_id]
        proposed = proposed_by_id.get(card_id, [])
        kind_after = None
        state_handling, lifecycle = 'preserve', 'preserve'
        owner_before = card['repertoire_id'] if card else None
        owner_after = owner_before
        retained_implicit_owner = bool(card and card.get('canonical_route_source') and owner_before != source.repertoire_id
                                      and not any(link['repertoire_id'] == owner_before for link in links))
        schedule = {column: card.get(column) for column in SCHEDULE_COLUMNS} if card else None
        if card is None and (card_id in current_ids or own_link):
            block('missing_card', card_id, 'A source membership has no card. Repair it before shortening.')
        if proposed:
            # Match the publication classifier's union across current repertoires.
            outside = other_steps_by_card[card_id]
            has_prefix = any(step.segment_kind == 'prefix' for step in proposed) or any(step['segment_kind'] == 'prefix' for step in outside)
            has_root = any(step.parent_card_id is None for step in proposed) or any(step['parent_card_id'] is None for step in outside)
            kind_after = 'prefix' if has_prefix else 'response'
            fresh_state = 'new' if has_root else 'locked'
            if card:
                reference = proposed[0]
                try:
                    payload_matches = _payload(card) == (' '.join(reference.starting_fen.split()[:4]), reference.moves, reference.trained_color)
                except (ValueError, TypeError, KeyError, AttributeError):
                    payload_matches = False
                if (not payload_matches
                        or card.get('content_type') != 'opening' or card.get('archived') or card.get('superseded_by')
                        or card.get('pending_validation')):
                    block('incompatible_target', card_id, 'Existing target content, color, archival, or validation state is incompatible. Resolve it before shortening.')
                if card.get('canonical_route_source') and owner_before == source.repertoire_id and own_link is None:
                    block('provenance_collision', card_id, 'Adding a generated owner membership would suppress existing authored fallback. Resolve provenance explicitly before shortening.')
                if not card.get('canonical_route_source'):
                    unselected_reference = any(step.card_id == card_id and step.line_id not in selected for step in current_steps)
                    if card.get('kind') != kind_after and (retained_links or unselected_reference):
                        block('shared_metadata_change', card_id, 'Publication would change a shared or unselected card role. Resolve the overlap before shortening.')
                    if (card.get('state') in {'new', 'locked'} and card.get('introduced_at') is None
                            and not reviews_by_card[card_id] and card['state'] != fresh_state):
                        block('existing_schedule_change', card_id, 'Publication would change existing scheduling state. Resolve the target before shortening.')
                else:
                    kind_after = card.get('kind')
            else:
                lifecycle, state_handling = 'create', 'fresh'
                owner_after = source.repertoire_id
                schedule = {'state': fresh_state, 'due_date': snapshot.study_day,
                            'schedule_seed': None, 'initialization': 'postgres_graph_defaults'}
            classification = 'unchanged' if card_id in current_ids else 'reuse_existing' if card else 'new_replacement'
        elif card_id in current_ids and depth_changes:
            if own_link is None or own_link.get('canonical_route_source') != 0:
                block('authored_membership', card_id, 'Shortening would remove an authored membership. Resolve authored ownership explicitly first.')
            elif retained_links or retained_implicit_owner:
                classification = 'retained_shared'
                if owner_before == source.repertoire_id:
                    owner_after = min(link['repertoire_id'] for link in retained_links)
            else:
                classification, lifecycle = 'retired', 'archive'
                retired_ids.add(card_id)
        else:
            classification = 'unchanged'
            if depth_changes and own_link and own_link.get('canonical_route_source') == 0:
                block('unrelated_cleanup', card_id, 'Publication would remove a generated membership outside the current graph. Repair publication before shortening.')
        if not proposed and card_id in current_ids and depth_changes and card_id in conflicting_ids:
            classification = 'conflicting'
        for link in links:
            obsolete = link['repertoire_id'] == source.repertoire_id and card_id in current_ids and not proposed and bool(depth_changes)
            membership_plans.append(MembershipDisposition(
                repertoire_id=link['repertoire_id'], card_id=card_id,
                action='blocked' if obsolete and card_id in conflicting_ids else 'obsolete' if obsolete else 'preserve',
                canonical_route_source=link['canonical_route_source']))
        if proposed and own_link is None:
            membership_plans.append(MembershipDisposition(repertoire_id=source.repertoire_id, card_id=card_id,
                                                          action='add_generated', canonical_route_source=0))
        if card_id in conflicting_ids:
            classification, lifecycle, state_handling = 'conflicting', 'blocked', 'blocked'
        card_plans.append(CardDisposition(
            card_id=card_id, classification=classification, expected_revision=card['revision'] if card else None,
            lifecycle=lifecycle, owner_before=owner_before, owner_after=owner_after, kind_after=kind_after,
            state_handling=state_handling, schedule_json=canonical_json(schedule) if schedule else None,
            schedule_seed_json=canonical_json(seeds_by_card[card_id]) if card_id in seeds_by_card else None,
            review_ids=tuple(sorted(reviews_by_card[card_id]))))
        yield
    attempts = []
    for table, kind, identity_column in (
        ('daily_queue', 'queue', 'id'), ('queue_attempt_origins', 'origin', 'queue_entry_id'),
        ('opening_evidence_attempts', 'opening_attempt', 'attempt_id'), ('study_attempts', 'study_attempt', 'id'),
        ('pending_commands', 'pending_command', 'operation_id'), ('review_attempt_receipts', 'receipt', 'attempt_id'),
    ):
        for row in snapshot.rows(table):
            card_id = row['card_id']
            if card_id not in affected_ids:
                continue
            if kind == 'receipt':
                action = 'replay_receipt'
            elif card_id in conflicting_ids:
                action = 'blocked'
            elif kind == 'origin':
                action = 'retain_for_recovery'
            elif card_id in retired_ids:
                action = ('supersede_projection' if kind == 'queue' and row['status'] == 'queued'
                          else 'retire_with_conflict' if kind == 'pending_command'
                          or kind == 'opening_attempt' and row['state'] != 'complete'
                          or kind == 'study_attempt' and row.get('finalized_at') is None
                          else 'preserve')
            else:
                action = 'preserve'
            object_id = str(row[identity_column])
            if kind == 'origin':
                object_id += f":{row['revision']}"
            attempts.append(AttemptDisposition(kind=kind, object_id=object_id, card_id=card_id, action=action))
            yield
    submissions = tuple(SubmissionDisposition(
        card_id=card.card_id, expected_revision=card.expected_revision,
        unresolved_submission='blocked' if card.card_id in conflicting_ids else
        'archived_identity_conflict' if card.card_id in retired_ids else 'existing_identity_recovery')
        for card in card_plans if card.expected_revision is not None)
    contents = dict(repertoire_id=source.repertoire_id, snapshot_id=snapshot_identity(source),
                    transition_snapshot_id=transition_snapshot_identity(snapshot), graph_generation=source.graph_generation,
                    graph_policy_version=POLICY_VERSION, position_version=POSITION_VERSION, structural_version=EVALUATION_VERSION,
                    study_day=snapshot.study_day, status='blocked' if blockers else 'ready' if depth_changes else 'no_op',
                    selected_line_ids=tuple(sorted(selected)),
                    depth_changes=depth_changes, current_steps=current_steps, proposed_steps=proposed_steps,
                    cards=tuple(card_plans), memberships=tuple(sorted(membership_plans, key=lambda item: (item.card_id, item.repertoire_id))),
                    attempts=tuple(sorted(attempts, key=lambda item: (item.card_id, item.kind, item.object_id))),
                    submissions=submissions, blockers=tuple(sorted(blockers, key=lambda item: (item.object_id, item.code))))
    plan = PrefixTransitionPlan(plan_id='', **contents)
    return plan.model_copy(update={'plan_id': stable_key('prefix-transition-plan', TRANSITION_VERSION, plan.model_dump(mode='json', exclude={'plan_id'}))})


def plan_transition(snapshot, selected_line_ids, candidate_depths):
    """Public pure entry point: derive structure using the production evaluator."""
    validate_shortening(snapshot.source, selected_line_ids, candidate_depths)
    evaluation = evaluate_prefix(snapshot.source, selected_line_ids, candidate_depths)
    calculation = iter_transition_plan(snapshot, selected_line_ids, candidate_depths, evaluation)
    while True:
        try:
            next(calculation)
        except StopIteration as completed:
            return completed.value
