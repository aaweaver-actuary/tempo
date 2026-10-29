"""Game Review uses public owner receipt reads; GET never starts producer work."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.contracts.product_v02.learning import EncounterReceipt, GameEffectControl, GameLapse, LearningReceipt
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


def get_game(case):
    app = FastAPI()
    app.include_router(create_game_review_router(case.review))
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
            return await client.get("/api/product/game-review/games/lichess/" + case.source.normalized.game_id)
    return asyncio.run(request())


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


def test_game_review_owner_status_failure_does_not_publish_cached_availability():
    case = scenario()
    case.owner.unavailable = True
    response = get_game(case)
    assert response.status_code == 503
    assert response.json() == {"detail": "owner_receipt_unavailable"}
    assert case.store.calls == ["source", "deliveries"]
