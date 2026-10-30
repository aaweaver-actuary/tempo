"""Game Review uses public owner receipt reads; GET never starts producer work."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.contracts.product_v02.games import CanonicalGame
from app.contracts.product_v02.learning import EncounterReceipt, GameEffectControl, GameEncounter, GameLapse, LearningReceipt
from app.contracts.product_v02.presentation import GameDetail
from app.games.feedback.coordinator import delivery_for
from app.games.feedback.http import create_game_review_router
from app.games.feedback.ports import SourceState
from app.games.feedback.review import GameReview, ReviewUnavailable
from test_product_encounter_projection import inputs, project
from test_product_feedback_jobs import harness, spec
from test_product_game_findings import record


def forbidden(*args, **kwargs):
    pytest.fail("Game Review GET called a mutation, acquisition or queue operation")


class ReceiptOwner:
    def __init__(self, current, *, legacy=False):
        self.current, self.legacy = current, legacy
        self.calls, self.unavailable = [], False

    def encounter_status(self, key):
        assert not self.legacy, "Legacy delivery changed its receipt wire form"
        self.calls.append(("encounter", key))
        if self.unavailable:
            raise ReviewUnavailable("owner_receipt_unavailable", 503)
        return self.current

    def effect_status(self, key):
        self.calls.append(("legacy", key))
        if self.unavailable:
            raise ReviewUnavailable("owner_receipt_unavailable", 503)
        # The legacy owner operation intentionally exposes only its base fields.
        return LearningReceipt(**{name: getattr(self.current, name)
                                  for name in LearningReceipt.model_fields})

    accept_encounter = accept_lapse = control_effect = staticmethod(forbidden)


class ReadOnlySource:
    def __init__(self, source, deliveries):
        self.source, self.saved_deliveries = source, deliveries
        self.calls = []

    def source_detail(self, workspace, provider, game_id):
        self.calls.append("source")
        assert (workspace, provider, game_id) == (self.source.normalized.workspace_id,
            "lichess", self.source.normalized.game_id)
        return self.source

    def deliveries(self, source_key):
        self.calls.append("deliveries")
        assert source_key == self.source.source_key
        return self.saved_deliveries

    def __getattr__(self, name):
        forbidden(name)


def scenario(*, admission="pending", legacy=False, control=False, findings=True):
    if legacy:
        saved = harness()
        saved.store.enqueue(spec())
        assert saved.runner.run_once() == "page"
        source, = saved.store.sources.values()
        command, = source.effects
        assert isinstance(command, GameLapse)
        game, revision = source.normalized, command.finding.source_revision
    else:
        game, snapshot, compared, source_scope = inputs(count=2)
        encounter, = project(game, snapshot, compared, source_scope)
        command, revision = encounter, encounter.source.source_revision
        source = SourceState(source_key="stored-source", original=record(("d2d4",)),
            record_fingerprint="captured-original", normalized=game,
            findings=compared if findings else (), comparison_revision="captured-comparison",
            effects=(command,), encounters=(encounter,))
    common = dict(key=command.key, source_revision=revision,
        status="applied" if admission == "available" else "held" if control else "pending",
        outcome="duplicate", owner_receipt_id="actual-owner-receipt",
        admitted_at=None, available_by=None, applied_at=None,
        reason=None if admission == "available" else "authority_hold" if control else "study_priority_pending")
    current = (LearningReceipt(**common) if legacy else EncounterReceipt(**common,
        detected_at=encounter.detected_at, source_digest=encounter.source.source_digest,
        observation=encounter.observation, observation_recorded=True, disposition="first_introduction",
        admission_status=admission, deferred_reason="authority_unavailable" if control else None))
    delivery_command = (GameEffectControl(key=command.key, source_revision=revision,
        action="hold", reason="reanalysis") if control else command)
    # A previously persisted receipt can be older than the public owner status.
    cached = (current if legacy else current.model_copy(update={
        "status": "applied", "admission_status": "available", "deferred_reason": None, "reason": None}))
    delivery = delivery_for(source.source_key, delivery_command).model_copy(update={
        "state": "acknowledged", "receipt": cached})
    store, owner = ReadOnlySource(source, (delivery,)), ReceiptOwner(current, legacy=legacy)
    review = GameReview(workspace=game.workspace_id, store=store, learning=owner,
        build_job=forbidden, deliver=forbidden, now=lambda: 0)
    return SimpleNamespace(source=source, store=store, owner=owner, review=review,
        receipt=current, delivery=delivery, command=command)


def get_game(case, repertoire_id=None):
    app = FastAPI()
    app.include_router(create_game_review_router(case.review))
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
            return await client.get("/api/product/game-review/games/lichess/" + case.source.normalized.game_id,
                                    params={"repertoire_id": repertoire_id} if repertoire_id else {})
    return asyncio.run(request())


def selected_scenario():
    """Use B1's shared contradictory-diagnostic/global-boundary read fixture."""
    fixture = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures/game-detail/own_actionable.json"
    detail = GameDetail.model_validate_json(json.dumps(json.loads(fixture.read_text())["payload"]))
    selection = detail.first_departure
    canonical = CanonicalGame(workspace_id=selection.workspace_id, game_id=detail.game_id,
        game_revision=1, source_id=detail.provider, source_revision=selection.source.source_revision,
        source_digest=selection.source.source_digest, starting_fen=detail.starting_fen,
        moves=list(detail.moves), learner_color=detail.player_color, played_at=detail.played_at)
    original = record(detail.moves).model_copy(update={"source_revision": detail.source_revision})
    source = SourceState(source_key="stored-selected-source", original=original,
        record_fingerprint=original.source_digest, normalized=canonical, findings=detail.findings,
        comparison_revision=detail.comparison_revision, first_departure=selection, determined_at=detail.determined_at)
    store = ReadOnlySource(source, ())
    owner = SimpleNamespace(encounter_status=forbidden, effect_status=forbidden,
                            accept_encounter=forbidden, accept_lapse=forbidden, control_effect=forbidden)
    review = GameReview(workspace=canonical.workspace_id, store=store, learning=owner,
        build_job=forbidden, deliver=forbidden, now=forbidden)
    return SimpleNamespace(source=source, store=store, owner=owner, review=review, detail=detail)


def test_game_review_http_returns_retained_global_boundary_independent_of_diagnostic_filter():
    case = selected_scenario()
    before = case.source.model_dump_json()
    for repertoire in (None, "white", "unrelated-repertoire"):
        response = get_game(case, repertoire)
        assert response.status_code == 200, response.text
        detail = GameDetail.model_validate_json(response.text)
        assert detail.first_departure == case.source.first_departure
        assert detail.first_departure.ply == 3 and case.source.findings[0].ply == 1
        assert detail.determined_at == case.source.determined_at
        assert detail.comparison_revision == case.source.comparison_revision
        assert detail.comparison_freshness == "unknown"  # Shared fixture's current label is not persisted evidence.
        assert detail.source_revision == 23 and detail.first_departure.source.source_revision == 7
        assert detail.findings == (() if repertoire == "unrelated-repertoire" else case.source.findings)
    assert case.source.model_dump_json() == before
    assert case.store.calls == ["source", "deliveries"] * 3


def test_game_review_legacy_empty_findings_remain_unknown_without_a_recorded_selection():
    case = selected_scenario()
    case.source = case.source.model_copy(update={"first_departure": None, "determined_at": None, "findings": ()})
    case.store.source = case.source
    response = get_game(case)
    assert response.status_code == 200, response.text
    detail = GameDetail.model_validate_json(response.text)
    assert detail.first_departure is None and detail.determined_at is None
    assert detail.comparison_freshness == "unknown" and detail.receipts == ()


@pytest.mark.parametrize("change", [
    {"workspace_id": "different-workspace"},
    {"source": {"source_revision": 8}},
    {"source": {"source_digest": "e" * 64}},
])
def test_game_review_rejects_selection_bound_to_a_different_canonical_source(change):
    case = selected_scenario()
    if "source" in change:
        change = {"source": case.source.first_departure.source.model_copy(update=change["source"])}
    case.source = case.source.model_copy(update={"first_departure": case.source.first_departure.model_copy(update=change)})
    case.store.source = case.source
    response = get_game(case)
    assert response.status_code == 409
    assert response.json() == {"detail": "comparison_source_identity_mismatch"}
    assert case.store.calls == ["source"]


def mixed_receipt_scenario(stage="again"):
    case = selected_scenario()
    legacy, encounter = case.detail.receipts
    selected = case.source.first_departure
    progress = encounter.progress.model_dump(mode="json")
    changes = {}
    if stage == "pending":
        progress.update(scheduling_review_id=None, schedule_applied_at=None, remediation_fence=None)
        changes.update(status="pending", admission_status="pending", reason="study_priority_pending", applied_at=None)
    if stage == "introduction":
        progress.update(scheduling_review_id=None, schedule_applied_at=None,
                        introduction_applied_at=encounter.progress.schedule_applied_at)
        changes["disposition"] = "first_introduction"
    if stage in {"issued", "settled"}:
        attempt = dict(queue_entry_id=9, card_id=progress["target"]["card_id"], card_revision=1,
                       issuance_sequence=101, issued_at="2026-10-26T12:02:00Z")
        progress.update(first_issued=attempt, first_issued_ordinal=1)
        if stage == "settled":
            progress["settlement"] = dict(review_id=42, attempt=attempt)
            changes.update(status="settled", admission_status="settled")
    if stage == "remapped":
        progress["prior_remediation_fences"] = [progress["remediation_fence"]]
        progress["target"] = dict(card_id="current-joined-presentation", card_revision=2)
        progress["remediation_fence"] = dict(target=progress["target"], after_issuance_sequence=102,
                                             established_at="2026-10-26T12:03:00Z")
    payload = encounter.model_dump(mode="json") | changes | {"progress": progress}
    encounter = EncounterReceipt.model_validate_json(json.dumps(payload))
    legacy_command = GameLapse(key=legacy.key,
        finding=case.source.findings[0].model_copy(update={"source_revision": legacy.source_revision}),
        detected_at=case.source.determined_at, occurred_at=case.source.original.played_at,
        payload_digest=case.source.original.source_digest)
    encounter_command = GameEncounter(key=encounter.key, source=selected.source, decision=selected.decision,
        expected_uci=selected.expected_uci[0], played_uci=selected.played_uci, observation="missed",
        detected_at=case.source.determined_at, occurred_at=case.source.original.played_at,
        payload_digest=case.source.original.source_digest)
    case.source = case.source.model_copy(update={"effects": (legacy_command, encounter_command),
                                                 "encounters": (encounter_command,)})
    case.store.source = case.source
    case.store.saved_deliveries = tuple(delivery_for(case.source.source_key, command)
        for command in (legacy_command, encounter_command))
    case.owner.current = {legacy.key.logical_id(): legacy, encounter.key.logical_id(): encounter}
    def status(key):
        return case.owner.current.get(key.logical_id())
    case.owner.effect_status = case.owner.encounter_status = status
    case.expected_receipts = (legacy, encounter)
    return case


@pytest.mark.parametrize("stage", ["pending", "again", "introduction", "issued", "settled", "remapped"])
def test_game_review_preserves_mixed_receipt_stages_and_original_effect_across_target_remap(stage):
    case = mixed_receipt_scenario(stage)
    before = case.source.model_dump_json()
    response = get_game(case, "unrelated-repertoire")
    assert response.status_code == 200, response.text
    detail = GameDetail.model_validate_json(response.text)
    assert detail.receipts == case.expected_receipts
    assert type(detail.receipts[0]) is LearningReceipt and isinstance(detail.receipts[1], EncounterReceipt)
    assert detail.first_departure == case.source.first_departure and detail.findings == ()
    if stage == "remapped":
        assert detail.receipts[1].progress.target.card_id != detail.first_departure.decision.presentation_id
        assert detail.receipts[1].key == case.source.encounters[0].key
        assert detail.receipts[1].progress.scheduling_review_id == 41
    assert case.source.model_dump_json() == before


@pytest.mark.parametrize("mismatch", ["key", "revision", "digest"])
def test_game_review_does_not_associate_same_card_receipt_from_an_unproved_original_source(mismatch):
    case = mixed_receipt_scenario()
    current = case.expected_receipts[1]
    changes = {"key": current.key.model_copy(update={"decision_id": "unrelated-decision"})} if mismatch == "key" else (
        {"source_revision": 8} if mismatch == "revision" else {"source_digest": "e" * 64})
    case.owner.current[current.key.logical_id()] = current.model_copy(update=changes)
    response = get_game(case)
    assert response.status_code == 200, response.text
    assert GameDetail.model_validate_json(response.text).receipts == (case.expected_receipts[0],)


@pytest.mark.parametrize("findings", [True, False])
def test_game_review_http_preserves_current_pending_encounter_without_claiming_availability(findings):
    case = scenario(findings=findings)
    original = case.source.model_dump_json()
    response = get_game(case)
    assert response.status_code == 200, response.text
    actual, = response.json()["receipts"]
    assert actual == case.receipt.model_dump(mode="json")
    assert actual["admission_status"] == "pending" and actual["available_by"] is None
    parsed = GameDetail.model_validate_json(response.text)
    assert isinstance(parsed.receipts[0], EncounterReceipt)
    assert parsed.played_at == case.source.original.played_at
    assert parsed.time_precision == case.source.original.time_precision
    assert case.owner.calls == [("encounter", case.command.key)]
    assert case.store.calls == ["source", "deliveries"]
    assert case.source.model_dump_json() == original


def test_game_review_reads_available_from_public_encounter_status():
    case = scenario(admission="available")
    response = get_game(case)
    assert response.status_code == 200, response.text
    assert response.json()["receipts"] == [case.receipt.model_dump(mode="json")]
    assert case.owner.calls == [("encounter", case.command.key)]


def test_game_review_control_delivery_keeps_original_encounter_status_and_deferral():
    case = scenario(control=True)
    assert case.delivery.encounter is None and case.delivery.control is not None
    response = get_game(case)
    assert response.status_code == 200, response.text
    actual, = response.json()["receipts"]
    assert actual == case.receipt.model_dump(mode="json")
    assert actual["status"] == "held" and actual["admission_status"] == "pending"
    assert actual["deferred_reason"] == "authority_unavailable"
    assert case.owner.calls == [("encounter", case.command.key)]


def test_game_review_legacy_receipt_json_is_unchanged():
    case = scenario(legacy=True)
    response = get_game(case)
    assert response.status_code == 200, response.text
    assert response.json()["receipts"] == [case.receipt.model_dump(mode="json")]
    assert case.owner.calls == [("legacy", case.command.key)]


def test_game_review_qualified_legacy_lapse_keeps_current_progress_receipt():
    from app.contracts.product_v02.learning import FirstDepartureWitness
    from test_product_delivery_qualification import QualifiedReceiver
    case = scenario(legacy=True)
    witness = FirstDepartureWitness(selection=case.source.first_departure,
        configuration_revision=1,determined_at=case.source.determined_at,stream="reconciliation")
    current = QualifiedReceiver().accept_qualified(case.command,qualification=witness)
    qualified = case.delivery.model_copy(update={"qualification_status":"ready",
        "qualification":witness,"determined_at":witness.determined_at,"receipt":current})
    case.store.saved_deliveries = (qualified,)
    case.owner.legacy,case.owner.current = False,current
    response = get_game(case)
    assert response.status_code == 200,response.text
    assert response.json()["receipts"] == [current.model_dump(mode="json")]
    parsed = GameDetail.model_validate_json(response.text)
    assert parsed.receipts[0].progress.determined_at == witness.determined_at
    assert case.owner.calls == [("encounter",case.command.key)]


def test_game_review_owner_status_failure_does_not_publish_cached_availability():
    case = scenario()
    case.owner.unavailable = True
    response = get_game(case)
    assert response.status_code == 503
    assert response.json() == {"detail": "owner_receipt_unavailable"}
    assert case.store.calls == ["source", "deliveries"]
