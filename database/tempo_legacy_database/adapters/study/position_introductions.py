"""Fresh position state for a genuinely authorized physical introduction.

Call the prelock fence before any Study card/day/queue lock, then apply only
if the existing introduction admission succeeds in the same borrowed writer.
Seed outcomes remain immutable historical evidence.
"""
from dataclasses import dataclass
from datetime import datetime

from tempo_evidence_math.position_learning import Hold


_FENCE_SEAL = object()
_INTRO_SEAL = object()


@dataclass(frozen=True, init=False)
class IntroductionFence:
    epoch: str
    definitions: tuple
    prepared: object
    _seal: object

    def __init__(self, seal, prepared, epoch, definitions):
        if seal is not _FENCE_SEAL:
            raise ValueError('position_introduction_fence_required')
        object.__setattr__(self, 'epoch', epoch)
        object.__setattr__(self, 'definitions', definitions)
        object.__setattr__(self, 'prepared', prepared)
        object.__setattr__(self, '_seal', seal)


@dataclass(frozen=True, init=False)
class PreparedIntroduction:
    source: object
    card_id: str
    definitions: tuple
    bindings: tuple
    manifest_digest: str
    manifest_count: int
    _seal: object

    def __init__(self, seal, source, card_id, definitions, bindings, manifest_digest, manifest_count):
        if seal is not _INTRO_SEAL:
            raise ValueError('position_introduction_preparation_required')
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'card_id', card_id)
        object.__setattr__(self, 'definitions', definitions)
        object.__setattr__(self, 'bindings', bindings)
        object.__setattr__(self, 'manifest_digest', manifest_digest)
        object.__setattr__(self, 'manifest_count', manifest_count)
        object.__setattr__(self, '_seal', seal)


def prepare_introduction(source, card_id, *, target=None):
    """Project the bounded exact decisions and aliases of an admitted card."""
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import PreparedPositionSource
    if not isinstance(source, PreparedPositionSource) or not card_id:
        return Hold('position_source_owner_preparation_required')
    rep = source.authored_token.repertoire_id
    own = [b for b in source.snapshot.decision_presentations
           if b.repertoire_id == rep and b.presentation_id == card_id and b.available]
    if not own:
        return Hold('position_introduction_binding_unavailable')
    if target is not None:
        own = [b for b in own if b.position_key == target.key.position
               and b.selected_response_uci == target.expected_uci]
        if not own or target.key.repertoire_id != rep:
            return Hold('position_introduction_target_unavailable')
    keys = {(b.position_key, b.selected_response_uci) for b in own}
    import hashlib
    import json
    definitions = {}
    manifest = {}
    for route in source.routes:
        for decision in route.decisions:
            d = decision.definition
            manifest_key = (d.key.variant, d.key.position)
            prior_manifest = manifest.setdefault(manifest_key, d)
            if d.key.repertoire_id != rep or prior_manifest != d:
                return Hold('position_introduction_definition_ambiguous')
            if (d.key.position, d.expected_uci) in keys:
                prior = definitions.setdefault(d.key, d)
                if prior != d:
                    return Hold('position_introduction_definition_ambiguous')
    if (len(definitions) != len(keys) or not 1 <= len(definitions) <= 20
            or (target is not None and tuple(definitions.values()) != (target,))):
        return Hold('position_introduction_definition_unavailable')
    bindings = tuple(b for b in source.snapshot.decision_presentations
                     if b.repertoire_id == rep and b.position_key in {d.key.position for d in definitions.values()})
    if len({b.presentation_id for b in bindings}) > 100:
        return Hold('position_introduction_alias_limit')
    material = [[k[0], k[1], manifest[k].expected_uci, manifest[k].policy_version]
                for k in sorted(manifest)]
    digest = hashlib.sha256(json.dumps(material, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return PreparedIntroduction(_INTRO_SEAL, source, card_id,
        tuple(definitions[k] for k in sorted(definitions, key=lambda k: (k.variant, k.position))),
        bindings, digest, len(manifest))


def fence_introduction(database, prepared):
    """Fence source, owner, seed qualification and exact unseen aliases."""
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import fence_position_source
    from tempo_legacy_database.adapters.study.position_owner_epoch import owner_mode
    from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
    if not isinstance(prepared, PreparedIntroduction) or prepared._seal is not _INTRO_SEAL:
        return Hold('position_introduction_preparation_required')
    source = prepared.source
    held = fence_position_source(database, source)
    if held is not None:
        return held
    token = source.authored_token
    owner = owner_mode(database, token.repertoire_id, token.authored_digest)
    if owner.disposition != 'position':
        return Hold(owner.reason)
    fence_namespace(database)
    run = database.execute_native('''SELECT source_digest,source_token_json,selection_policy,allocator_preflight_json,status,
        manifest_digest,manifest_count,seeded_count,held_count FROM study_position_seed_runs
        WHERE repertoire_id=%s AND owner_epoch=%s FOR SHARE SKIP LOCKED''',
        (token.repertoire_id, owner.epoch)).fetchone()
    if not run or run['status'] != 'complete' or run['source_digest'] != token.authored_digest:
        return Hold('position_seed_preflight_unavailable')
    seed_token = run['source_token_json']
    if (not isinstance(seed_token, dict) or seed_token.get('repertoire_id') != token.repertoire_id
            or seed_token.get('authored_digest') != token.authored_digest
            or run['selection_policy'] != 'earliest_due_then_card_id:v1'
            or run['manifest_digest'] != prepared.manifest_digest
            or run['manifest_count'] != prepared.manifest_count):
        return Hold('position_seed_preflight_unavailable')
    preflight = run['allocator_preflight_json']
    if (not isinstance(preflight, dict) or preflight.get('complete') is not True
            or preflight.get('after') is not None or type(preflight.get('scanned')) is not int
            or preflight['scanned'] < 0 or run['seeded_count'] + run['held_count'] != run['manifest_count']):
        return Hold('position_seed_preflight_unavailable')
    missing, aliases = [], {}
    for definition in prepared.definitions:
        key = definition.key
        state = database.execute_native('''SELECT expected_uci,policy_version,owner_epoch,source_digest,introduced_at
            FROM study_position_states WHERE repertoire_id=%s AND variant=%s AND position=%s
            FOR UPDATE SKIP LOCKED''', (key.repertoire_id, key.variant, key.position)).fetchone()
        if state:
            if state['introduced_at'] is None:
                return Hold('position_existing_unintroduced_state_requires_reconciliation')
            if (state['expected_uci'], state['policy_version'], state['owner_epoch'], state['source_digest']) != (
                    definition.expected_uci, definition.policy_version, owner.epoch, token.authored_digest):
                return Hold('position_introduction_existing_state_changed')
            continue
        outcome = database.execute_native('''SELECT disposition,hold_reason,expected_uci,policy_version
            FROM study_position_seed_outcomes WHERE repertoire_id=%s AND owner_epoch=%s
              AND variant=%s AND position=%s FOR SHARE SKIP LOCKED''',
            (key.repertoire_id, owner.epoch, key.variant, key.position)).fetchone()
        if not outcome or (outcome['disposition'], outcome['hold_reason'], outcome['expected_uci'], outcome['policy_version']) != (
                'hold', 'no_valid_current_introduced_donor', definition.expected_uci, definition.policy_version):
            return Hold('position_introduction_seed_outcome_unavailable')
        matches = [b for b in prepared.bindings if b.position_key == key.position]
        if not matches or any(b.selected_response_uci != definition.expected_uci
                              or b.introduction != 'unintroduced' for b in matches):
            return Hold('position_introduction_history_ambiguous')
        for b in matches:
            previous = aliases.setdefault(b.presentation_id, b.presentation_revision)
            if previous != b.presentation_revision:
                return Hold('position_introduction_alias_changed')
        missing.append(definition)
    for card_id, revision in sorted(aliases.items()):
        lock = database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                       ('tempo:card-edit:' + card_id,)).fetchone()
        if not lock or not lock[0]:
            return Hold('position_introduction_card_busy')
        card = database.execute_native('''SELECT revision,introduced_at,state,archived,pending_validation,superseded_by
            FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED''', (card_id,)).fetchone()
        if (not card or card['revision'] != revision or card['introduced_at'] is not None
                or card['state'] not in ('new', 'locked') or card['archived']
                or card['pending_validation'] or card['superseded_by'] is not None):
            return Hold('position_introduction_history_changed')
        history = database.execute_native("""SELECT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=%s)
            OR EXISTS(SELECT 1 FROM opening_evidence_attempts a WHERE a.card_id=%s)
            OR EXISTS(SELECT 1 FROM queue_attempt_origins o WHERE o.card_id=%s AND
                (o.issued_as_head=1 OR o.attempt_failed<>0 OR o.review_result_json IS NOT NULL
                 OR o.last_status='complete'))""", (card_id, card_id, card_id)).fetchone()
        if not history or history[0]:
            return Hold('position_introduction_history_changed')
    return IntroductionFence(_FENCE_SEAL, prepared, owner.epoch, tuple(missing))


def initialize_introduction(database, prepared, fenced, *, now):
    """Create fresh FSRS state after the real introduction was admitted."""
    from fsrs import Card
    if (not isinstance(now, datetime) or now.utcoffset() is None
            or not isinstance(fenced, IntroductionFence) or fenced._seal is not _FENCE_SEAL
            or fenced.prepared is not prepared):
        raise ValueError('position_introduction_fence_required')
    token = prepared.source.authored_token
    for definition in fenced.definitions:
        allocated = database.execute_native("SELECT nextval('study_position_fsrs_id_seq')").fetchone()[0]
        card = Card(card_id=allocated, due=now)
        database.execute_native('''INSERT INTO study_position_states
            (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
             fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (definition.key.repertoire_id, definition.key.variant, definition.key.position,
             definition.expected_uci, definition.policy_version, fenced.epoch, token.authored_digest,
             allocated, card.to_json(), now, now))
