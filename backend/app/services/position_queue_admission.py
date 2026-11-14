"""Position queue carrier selection from an owner-prepared source.

The returned binding is a proposal only. The queue writer must recheck the
source, owner, card revision and admission under its existing locks.
"""

from datetime import UTC

from tempo_evidence_math.position_learning import Hold


def select_carrier(source, target, *, card_id=None):
    """Choose one current physical carrier for an exact authored decision.

    Aliases and multiple physical candidates within one repertoire are normal;
    choose deterministically without treating physical identity as schedule identity.
    """
    key = target.key
    if source.authored_token.repertoire_id != key.repertoire_id:
        return Hold("position_carrier_source_scope_mismatch")
    decisions = [
        decision
        for route in source.routes
        for decision in route.decisions
        if decision.definition.key == key
    ]
    if not decisions or any(d.definition != target for d in decisions):
        return Hold("position_carrier_definition_ambiguous")
    bindings = [
        binding
        for binding in source.snapshot.decision_presentations
        if binding.repertoire_id == key.repertoire_id
        and binding.position_key == key.position
    ]
    if any(
        binding.selected_response_uci != target.expected_uci for binding in bindings
    ):
        return Hold("position_carrier_response_ambiguous")
    eligible = [
        binding
        for binding in bindings
        if binding.available
        and binding.presentation_id
        and binding.presentation_revision > 0
    ]
    if card_id is not None:
        eligible = [
            binding for binding in eligible if binding.presentation_id == card_id
        ]
    if not eligible:
        return Hold("position_carrier_unavailable")
    return min(
        eligible,
        key=lambda binding: (
            binding.presentation_id,
            binding.presentation_revision,
            binding.decision_id,
        ),
    )


def prepare_due(database, day, payload):
    """Prepare one introduced due decision using bounded H1 reads outside the writer."""
    from datetime import date, datetime

    from tempo_storage.study.position_due_candidates import (
        PositionDueCandidateReader,
        PositionDueCursor,
    )
    from tempo_storage.study.position_owner_epoch import active_owner, next_active_owner

    from app.repertoire.position_study_source import prepare_position_source

    current_rep = payload.get("position_repertoire")
    if not current_rep:
        row = next_active_owner(database, payload.get("position_repertoire_after", ""))
    else:
        row = active_owner(database, current_rep)
        if row is None:
            return Hold("position_owner_changed_during_admission")
    if not row:
        return None
    repertoire, epoch, digest = row.repertoire_id, row.epoch, row.source_digest
    if current_rep and (
        payload.get("position_epoch"),
        payload.get("position_digest"),
    ) != (epoch, digest):
        return Hold("position_owner_changed_during_admission")
    instant = (
        datetime.fromisoformat(payload["position_instant"])
        if payload.get("position_instant")
        else datetime.now(UTC)
    )
    study_day = date.fromisoformat(day)
    reader = PositionDueCandidateReader(
        database,
        repertoire_id=repertoire,
        owner_epoch=epoch,
        source_digest=digest,
        issued_at=instant,
        study_day=study_day,
    )
    after = payload.get("position_after")
    cursor = (
        PositionDueCursor(
            repertoire,
            epoch,
            digest,
            instant,
            study_day,
            False,
            datetime.fromisoformat(after[0]),
            after[1],
            after[2],
        )
        if after
        else None
    )
    page = reader.read_due_page(after=cursor, limit=1)
    if isinstance(page, Hold):
        return page
    if not page.candidates:
        return {"repertoire_done": repertoire}
    candidate = page.candidates[0]
    source = prepare_position_source(database, repertoire)
    if isinstance(source, Hold):
        return source
    if source.authored_token.authored_digest != digest:
        return Hold("position_owner_source_changed")
    carrier = select_carrier(source, candidate.state.definition)
    if isinstance(carrier, Hold):
        return carrier
    state = candidate.state
    return {
        "source": source,
        "candidate": candidate,
        "carrier": carrier,
        "cursor": {
            "position_repertoire": repertoire,
            "position_epoch": epoch,
            "position_digest": digest,
            "position_instant": instant.isoformat(),
            "position_after": [
                state.due_at.isoformat(),
                state.definition.key.variant,
                state.definition.key.position,
            ],
        },
    }


def admit_due(database, day, prepared):
    """Fence one proposal before acquiring the ordinary card and date locks."""
    from datetime import date

    from tempo_storage.authored_repertoires.position_study_source import (
        fence_position_source,
    )
    from tempo_storage.queue_position_lock import (
        lock_queue_date_for_position,
    )
    from tempo_storage.queue_refresh import admissions
    from tempo_storage.study.position_due_candidates import (
        PositionDueCandidateReader,
    )
    from tempo_storage.study.position_owner_epoch import owner_mode

    source, candidate, carrier = (
        prepared["source"],
        prepared["candidate"],
        prepared["carrier"],
    )
    token, state = source.authored_token, candidate.state
    held = fence_position_source(database, source)
    if held is not None:
        return held
    owner = owner_mode(database, token.repertoire_id, token.authored_digest)
    if owner.disposition != "position":
        return Hold(owner.reason)
    if owner.epoch != state.owner_epoch:
        return Hold("position_owner_epoch_changed")
    reader = PositionDueCandidateReader(
        database,
        repertoire_id=token.repertoire_id,
        owner_epoch=state.owner_epoch,
        source_digest=token.authored_digest,
        issued_at=candidate.witness.issued_at,
        study_day=date.fromisoformat(day),
    )
    current = reader.states.lock_current(state.definition.key)
    if isinstance(current, Hold):
        return current
    witnesses = reader.read_context((state.definition.key,))
    if isinstance(witnesses, Hold) or isinstance(witnesses[0], Hold):
        return witnesses if isinstance(witnesses, Hold) else witnesses[0]
    if (
        current.definition != state.definition
        or current.version != state.version
        or not witnesses[0].due
    ):
        return Hold("position_due_changed")
    if admissions.locked_card(database, carrier.presentation_id) is None:
        return Hold("position_carrier_missing")
    lock_queue_date_for_position(database, day)
    carrier_status = admissions.position_carrier_eligible(
        database,
        token.repertoire_id,
        carrier.presentation_id,
        carrier.presentation_revision,
        day,
    )
    if carrier_status is None:
        return Hold("position_carrier_changed")
    if not carrier_status["occupied"]:
        admissions.append_position_due(
            database, day, token.repertoire_id, carrier.presentation_id
        )
    return None


def prepare_opening_introduction(database, planned):
    """Prepare position state only for the existing selected introduction."""
    from tempo_storage.study.position_introductions import (
        prepare_introduction,
    )
    from tempo_storage.study.position_owner_epoch import current_owner

    repertoire = planned["repertoire_id"]
    row = current_owner(database, repertoire)
    if row is None or row.mode in ("inactive", "shadow"):
        return ("legacy", repertoire)
    if row.mode != "active":
        return Hold("position_owner_" + row.mode)
    from .queue_next_card import _cached_position_source

    source = _cached_position_source(database, repertoire, row.epoch, row.source_digest)
    if isinstance(source, Hold):
        return source
    if source.authored_token.authored_digest != row.source_digest:
        return Hold("position_owner_source_changed")
    intro = prepare_introduction(source, planned["card_id"])
    return intro if isinstance(intro, Hold) else ("position", intro)


def fence_opening_introduction(database, prepared):
    from tempo_storage.study.position_introductions import (
        fence_introduction,
    )
    from tempo_storage.study.position_owner_epoch import owner_mode

    if prepared[0] == "legacy":
        owner = owner_mode(database, prepared[1], None)
        return None if owner.disposition == "legacy" else Hold(owner.reason)
    return fence_introduction(database, prepared[1])


def apply_opening_introduction(database, prepared, fenced, *, now):
    from tempo_scheduling import initial_position_state
    from tempo_storage.study.position_introductions import (
        initialize_introduction,
    )

    if prepared[0] == "position":
        initialize_introduction(
            database, prepared[1], fenced, now=now, initial_state=initial_position_state
        )


def redundant_opening_introduction(prepared, fenced):
    """A wholly introduced card grants no new position introduction."""
    return prepared[0] == "position" and not fenced.definitions


def prepare_targeted_position_introduction(source, target):
    """Prepare exactly the current qualified Games target, never its neighbors.

    J2 supplies its owner-validated target. The Repertoire capability must
    contain the same current physical and logical occurrence.
    """
    from tempo_storage.study.position_introductions import (
        prepare_introduction,
    )

    fields = (
        "decision_id",
        "repertoire_id",
        "position_key",
        "selected_response_uci",
        "presentation_id",
        "presentation_revision",
        "route_id",
        "route_ply",
    )
    try:
        matches = [
            binding
            for binding in source.snapshot.decision_presentations
            if all(getattr(binding, name) == getattr(target, name) for name in fields)
            and binding.available
        ]
    except AttributeError:
        return Hold("position_target_binding_unavailable")
    if len(matches) != 1:
        return Hold("position_target_binding_ambiguous")
    definitions = {
        d.definition
        for route in source.routes
        for d in route.decisions
        if d.definition.key.repertoire_id == target.repertoire_id
        and d.definition.key.position == target.position_key
        and d.definition.expected_uci == target.selected_response_uci
    }
    if len(definitions) != 1:
        return Hold("position_target_definition_ambiguous")
    prepared = prepare_introduction(
        source, target.presentation_id, target=next(iter(definitions))
    )
    return prepared if isinstance(prepared, Hold) else (prepared, target)


def fence_targeted_position_introduction(database, prepared_target):
    """Acquire the H2 introduction fences before J2 card/effect/day locks."""
    from tempo_storage.study.position_introductions import (
        fence_introduction,
    )

    prepared, _target = prepared_target
    fenced = fence_introduction(database, prepared)
    if isinstance(fenced, Hold):
        return fenced
    if not fenced.definitions:
        return Hold("position_target_already_introduced")
    return fenced


def admit_targeted_position_introduction(
    database, prepared_target, fenced, *, day, now, introductions
):
    """Use the existing encounter-exception admission and initialize atomically.

    The caller retains the transaction and must abort it on any exception.
    The capability is Study-owned and its result is the actual introduction
    receipt, not a fabricated review or Games scheduling effect.
    """
    from tempo_storage.study.position_introductions import (
        canonical_introduction_admitted,
        initialize_introduction,
    )

    prepared, target = prepared_target
    if getattr(fenced, "prepared", None) is not prepared or not fenced.definitions:
        raise ValueError("position_introduction_fence_required")
    result = introductions.admit_encounter(
        day,
        repertoire_id=target.repertoire_id,
        decision_id=target.decision_id,
        target=target,
    )
    if result.status == "introduced":
        admitted = canonical_introduction_admitted(
            database,
            card_id=target.presentation_id,
            revision=target.presentation_revision,
            day=day,
            repertoire_id=target.repertoire_id,
        )
        if not admitted:
            raise ValueError("position_canonical_introduction_unproved")
        from tempo_scheduling import initial_position_state

        initialize_introduction(
            database, prepared, fenced, now=now, initial_state=initial_position_state
        )
    elif result.status in {"already_introduced", "replayed"}:
        # This pre-fenced proposal represented unseen state, so an existing
        # physical receipt cannot authorize us to reset/replace a position.
        raise ValueError("position_introduction_raced_existing_admission")
    return result
