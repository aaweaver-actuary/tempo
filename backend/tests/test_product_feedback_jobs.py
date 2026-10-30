"""T08 workflow policy with injected capabilities; no provider/Study runtime."""
from __future__ import annotations

from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.product_v02.games import CanonicalGame, Finding, GamePage, GameRecord, GameRejection, GameWindow
from app.contracts.product_v02.learning import EncounterReceipt, GameEncounter, GameLapse, LearningReceipt
from app.contracts.product_v02.repertoire import IntroductionGameScope, RepertoireSnapshot
from app.games.feedback.coordinator import Coordinator, delivery_for, project_encounters, stable_id
from app.games.feedback.jobs import FeedbackRunner
from app.games.feedback.ports import AcquisitionFailure, ConcurrentUpdate, JobSpec, JobState

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures"


class FakeClock:
    def __init__(self):
        self.value = 1793016000.0
    def now(self):
        return self.value
    def utc_now(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat().replace("+00:00", "Z")


class MemoryStore:
    """Atomic fake at the semantic boundary; adapter transaction proof is separate."""
    def __init__(self):
        self.jobs, self.sources, self.deliveries, self.captures = {}, {}, {}, {}
        self.fail_commit = False
        self.pages = {}
    def enqueue(self, spec):
        prior = self.jobs.setdefault(spec.job_id, JobState(spec=spec))
        assert prior.spec == spec
        return prior
    def load_job(self, job_id):
        return self.jobs.get(job_id)
    def claim_job(self, now, lease_until):
        ready = [j for j in self.jobs.values() if (j.status in {"queued", "retryable"} and j.next_attempt_at <= now)
                 or (j.status == "running" and j.lease_until <= now)]
        if not ready:
            return None
        job = min(ready, key=lambda j: (j.attempts, j.next_attempt_at, j.spec.job_id))
        claimed = job.model_copy(update={"status": "running", "lease_until": lease_until, "attempts": job.attempts + 1})
        self.save_job(claimed)
        return self.jobs[job.spec.job_id]
    def save_job(self, job):
        if self.jobs[job.spec.job_id].version != job.version:
            raise ConcurrentUpdate()
        self.jobs[job.spec.job_id] = job.model_copy(update={"version": job.version + 1})
    def source(self, key):
        return self.sources.get(key)
    def commit_page(self, commit):
        if self.fail_commit:
            raise RuntimeError("injected_before_commit")
        assert self.jobs[commit.job.spec.job_id].version == commit.job.version
        for source in commit.sources:
            assert (self.sources[source.source_key].version if source.source_key in self.sources else 0) == source.version
        self.save_job(commit.job)
        if commit.provider_page_json is not None:
            self.pages.setdefault((commit.job.spec.job_id, commit.input_continuation), commit.provider_page_json)
        for source in commit.sources:
            self.sources[source.source_key] = source.model_copy(update={"version": source.version + 1})
        for capture in commit.captures:
            self.captures.setdefault((capture.source_key, capture.record.source_digest, capture.comparison_revision), capture)
        for delivery in commit.deliveries:
            if delivery.control:
                for key, prior in tuple(self.deliveries.items()):
                    if prior.command_kind in {"lapse", "encounter"} and prior.command.key == delivery.command.key and prior.state == "pending":
                        self.deliveries[key] = prior.model_copy(update={"state": "superseded"})
            self.deliveries.setdefault(delivery.operation_id, delivery)
    def pending_delivery(self, now):
        ready = [d for d in self.deliveries.values() if d.state == "pending" and d.next_attempt_at <= now]
        return min(ready, key=lambda d: (d.control is None, d.next_attempt_at, d.operation_id)) if ready else None
    def delivery(self, operation_id):
        return self.deliveries.get(operation_id)
    def save_delivery(self, delivery):
        prior = self.deliveries[delivery.operation_id]
        self.deliveries[delivery.operation_id] = delivery.model_copy(update={"state": "superseded"}) if prior.state == "superseded" else delivery


class Receiver:
    """Models owner receipts/fences, never a scheduler implementation."""
    def __init__(self):
        self.receipts, self.calls = {}, []
        self.uncertain = False
    def receipt(self, command, status, outcome):
        revision = command.source_revision if hasattr(command, "source_revision") else command.finding.source_revision
        return LearningReceipt(key=command.key, source_revision=revision, status=status, outcome=outcome,
            owner_receipt_id="owner-receipt-1", admitted_at=None, available_by=None, applied_at=None, reason=None)
    def accept_lapse(self, lapse):
        self.calls.append(lapse)
        key = lapse.key.logical_id()
        prior = self.receipts.get(key)
        if prior:
            return prior.model_copy(update={"outcome": "duplicate"})
        self.receipts[key] = self.receipt(lapse, "pending", "accepted")
        if self.uncertain:
            self.uncertain = False
            raise TimeoutError()
        return self.receipts[key]
    def control_effect(self, command):
        self.calls.append(command)
        self.receipts[command.key.logical_id()] = self.receipt(command, "held", "withdrawn")
        return self.receipts[command.key.logical_id()]
    def effect_status(self, key):
        return self.receipts.get(key.logical_id())


def record(revision=1, text="original"):
    return GameRecord(source_game_id="provider-game", source_revision=revision,
        source_digest=hashlib.sha256(text.encode()).hexdigest(), pgn=text,
        played_at="2026-10-25T12:00:00Z", time_precision="second")


def spec(job_id="job", provider="lichess"):
    return JobSpec(job_id=job_id, workspace_id="fixture", provider=provider, account="Learner",
        repertoire_ids=("white",), window=GameWindow(start_at="2026-10-01T00:00:00Z",
            end_at="2026-10-26T00:00:00Z", precision="second"))


def page(request=None, records=None, continuation=None, complete=True, rejections=()):
    request = request or spec()
    return GamePage(provider=request.provider, account=request.account, window=request.window,
        records=(record(),) if records is None else records, rejections=rejections, continuation=continuation,
        complete=complete, saturated=not complete)


def harness(store=None, *, kinds=("confirmed_miss",)):
    store, clock, receiver = store or MemoryStore(), FakeClock(), Receiver()
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(json.loads((FIXTURES / "snapshot.json").read_text())["payload"]))
    supplied_page = [page()]
    calls = []
    def fetch_page(**kwargs):
        calls.append(kwargs)
        value = supplied_page[0]
        if isinstance(value, Exception):
            raise value
        return value
    def normalize(value, request):
        if value.pgn == "invalid":
            raise ValueError("parse")
        return CanonicalGame(workspace_id=request.workspace_id, game_id=value.source_game_id,
            game_revision=value.source_revision, source_id=request.provider,
            source_revision=value.source_revision, source_digest=value.source_digest,
            starting_fen=snapshot.membership.routes[0].starting_fen,
            moves=["d2d4"], learner_color="white", played_at=value.played_at)
    def compare(game, _snapshot, *, record, spec):
        assert record.source_digest == game.source_digest and spec.workspace_id == game.workspace_id
        result = []
        for ply, kind in enumerate(kinds, 1):
            fixture = "confirmed-introduced-miss.json" if kind == "confirmed_miss" else (
                "opponent-gap.json" if kind == "opponent_gap" else "unintroduced.json")
            payload = json.loads((FIXTURES / fixture).read_text())["payload"]
            payload.update(provider="lichess", game_id=game.game_id, source_revision=game.source_revision,
                           source_digest=game.source_digest, ply=ply)
            result.append(Finding.model_validate_json(json.dumps(payload)))
        return tuple(result)
    coordinator = Coordinator(providers={"lichess": SimpleNamespace(fetch_page=fetch_page),
        "chesscom": SimpleNamespace(fetch_page=fetch_page)}, normalizer=SimpleNamespace(normalize=normalize),
        comparer=SimpleNamespace(compare=compare), snapshots=SimpleNamespace(snapshot=lambda _: snapshot),
        store=store, learning=receiver, clock=clock)
    runner = FeedbackRunner(coordinator)
    return SimpleNamespace(store=store, clock=clock, receiver=receiver, coordinator=coordinator,
        runner=runner, page=supplied_page, calls=calls)


def test_page_and_checkpoint_commit_together():
    h = harness()
    h.store.enqueue(spec())
    h.page[0] = page(continuation="safe-page-2", complete=False)
    assert h.runner.run_once() == "page"
    job = h.store.load_job("job")
    assert (job.continuation, job.window_complete, job.status) == ("safe-page-2", False, "queued")
    assert len(h.store.sources) == len(h.store.captures) == len(h.store.deliveries) == 1
    assert job.last_checkpoint_at == h.clock.utc_now()
    assert not h.receiver.calls
    assert len(h.store.pages) == 1


def test_crash_before_checkpoint_replays_without_duplicates():
    h = harness()
    h.store.enqueue(spec())
    h.store.fail_commit = True
    h.runner.run_once()
    assert not h.store.captures and h.store.load_job("job").continuation is None
    h.store.fail_commit = False
    h.clock.value += 5
    h.runner.run_once()
    h.runner.run_once()
    h.store.enqueue(spec("overlap"))
    h.runner.run_once()
    assert len(h.store.deliveries) == len(h.receiver.receipts) == 1


def test_crash_after_receiver_commit_reuses_effect_key():
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    h.receiver.uncertain = True
    h.runner.run_once()
    delivery = next(iter(h.store.deliveries.values()))
    assert delivery.state == "pending" and delivery.receipt is None
    h.clock.value += 3
    FeedbackRunner(h.coordinator).run_once()
    assert len(h.receiver.receipts) == 1
    assert h.receiver.calls[0] == h.receiver.calls[1]
    assert next(iter(h.store.deliveries.values())).receipt.outcome == "duplicate"


def test_recomparison_keeps_single_lapse():
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original = next(iter(h.store.deliveries.values())).lapse
    h.clock.value += 1000
    h.page[0] = page(records=(record(2, "original"),))
    h.store.enqueue(spec("overlap"))
    h.runner.run_once()
    h.runner.run_once()
    assert not next(iter(h.store.sources.values())).quarantined
    assert len(h.store.deliveries) == 1
    assert next(iter(h.store.deliveries.values())).lapse == original


def test_incomplete_window_never_reports_complete():
    h = harness()
    h.store.enqueue(spec())
    h.page[0] = page(complete=False)
    h.runner.run_once()
    job = h.store.load_job("job")
    assert job.status == "quarantined" and not job.window_complete and job.last_checkpoint_at is None
    second = harness()
    second.store.enqueue(spec())
    second.page[0] = page(records=(record(text="invalid"),), continuation="unsafe", complete=False)
    second.runner.run_once()
    job = second.store.load_job("job")
    assert job.continuation is None and not job.window_complete
    assert len(second.store.captures) == 1 and not second.store.deliveries


def test_provider_failure_does_not_starve_other_provider():
    h = harness(kinds=())
    h.store.enqueue(spec("a-fails"))
    other = spec("b-ready", "chesscom")
    h.store.enqueue(other)
    h.coordinator.providers["lichess"] = SimpleNamespace(fetch_page=lambda **_: (_ for _ in ()).throw(AcquisitionFailure("unavailable")))
    h.page[0] = page(other, records=())
    h.runner.run_once()
    h.runner.run_once()
    assert h.store.load_job("a-fails").status == "retryable"
    assert h.store.load_job("b-ready").status == "complete"


def test_retry_after_and_cancel_use_injected_clock():
    h = harness()
    h.store.enqueue(spec())
    h.page[0] = AcquisitionFailure("limited", retry_after=90)
    h.runner.run_once()
    assert h.store.load_job("job").next_attempt_at == h.clock.now() + 90
    assert h.runner.run_once() == "idle"
    h.clock.value += 90
    h.page[0] = page()
    def cancel_in_fetch(**_):
        h.runner.request_stop()
        return h.page[0]
    h.coordinator.providers["lichess"] = SimpleNamespace(fetch_page=cancel_in_fetch)
    h.runner.run_once()
    assert h.store.load_job("job").status == "queued" and not h.store.captures


def test_changed_source_is_quarantined_without_history_reversal():
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original = next(iter(h.store.deliveries.values())).lapse
    h.page[0] = page(records=(record(2, "corrected"),))
    h.coordinator.comparer = SimpleNamespace(compare=lambda *_, **__: ())  # No remaining confirmed miss.
    h.store.enqueue(spec("changed"))
    FeedbackRunner(h.coordinator).run_once()
    assert h.store.load_job("changed").status == "quarantined"
    assert h.store.load_job("changed").window_complete  # Other games/window acquisition can finish.
    h.receiver.uncertain = False
    h.runner.run_once()
    control = next(d.control for d in h.store.deliveries.values() if d.control)
    assert control.withdrawal is None and control.reason == "source_corrected"
    receipt = h.receiver.effect_status(original.key)
    assert receipt.status == "held"
    # Delayed original delivery hits the owner's persistent fence.
    restarted_receiver = Receiver()
    restarted_receiver.receipts = dict(h.receiver.receipts)
    assert restarted_receiver.accept_lapse(original).status == "held"
    assert len(h.receiver.receipts) == 1
    assert len(h.store.captures) == 2
    assert sum(d.lapse is not None for d in h.store.deliveries.values()) == 1
    assert next(d for d in h.store.deliveries.values() if d.lapse).state == "superseded"
    h.store.enqueue(spec("withdrawn"))
    claimed = h.store.claim_job(h.clock.now(), h.clock.now() + 120)
    h.coordinator.withdraw_source(claimed, next(iter(h.store.sources)))
    withdrawal = next(d.control for d in h.store.deliveries.values() if d.control and d.control.action == "withdraw")
    assert withdrawal.withdrawal is None and withdrawal.reason == "source_deleted"


def test_findings_preserve_gap_and_unintroduced_status():
    h = harness(kinds=("opponent_gap", "known_unintroduced"))
    h.store.enqueue(spec())
    h.runner.run_once()
    assert [f.kind for f in next(iter(h.store.sources.values())).findings] == ["opponent_gap", "known_unintroduced"]
    assert not h.store.deliveries


def test_no_learning_sql_or_study_runtime_dependency():
    root = Path(__file__).resolve().parents[1] / "app/games/feedback"
    for name in ("coordinator.py", "jobs.py", "ports.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # This public value-only interface is the adopted semantic seam;
                # its import-purity guard separately proves no Study startup.
                if module == "app.study.interfaces.game_priority":
                    assert {item.name for item in node.names} <= {
                        "EncounterFeedbackPort", "EncounterReceipt", "GameEffectControl", "GameEffectKey",
                        "GameEncounter", "GameLapse", "LearningFeedbackPort", "LearningReceipt"}
                    continue
                assert not module.startswith(("app.study", "app.services", "app.main", "tempo_legacy_database", "psycopg"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"execute", "executemany", "cursor"}
    # Policy executes with no provider, snapshot, comparer or receiver implementation.
    assert FeedbackRunner(harness().coordinator).run_once() == "idle"


def test_shutdown_releases_only_owned_resources():
    h = harness()
    owned, borrowed = [], []
    h.coordinator.providers["lichess"].close = lambda: borrowed.append(True)
    runner = FeedbackRunner(h.coordinator, owned_resources=(SimpleNamespace(close=lambda: owned.append(True)),))
    runner.request_stop()
    assert runner.run_once() == "stopped"
    runner.close()
    assert owned == [True] and borrowed == []


def test_rejection_does_not_advance_past_parsing_boundary():
    h = harness()
    h.store.enqueue(spec())
    h.page[0] = page(continuation="beyond-bad-record", complete=False,
        rejections=(GameRejection(source_game_id=None, source_revision=None, reason="parse_failure"),))
    h.runner.run_once()
    job = h.store.load_job("job")
    assert job.status == "quarantined" and job.continuation is None and not job.window_complete
    assert job.rejections[0].reason == "parse_failure"


def test_provider_extension_provenance_survives_base_projection():
    class ProviderRecord(GameRecord):
        source_metadata: tuple[tuple[str, str], ...]
    class ProviderPage(GamePage):
        records: tuple[ProviderRecord, ...]
        filters: tuple[tuple[str, str], ...]
    h = harness()
    request = spec().model_copy(update={"filters": (("rated", "true"),)})
    h.store.enqueue(request)
    extended = ProviderRecord(**record().model_dump(), source_metadata=(("source_fingerprint", "raw-provider-json-hash"),))
    h.page[0] = ProviderPage(**page(records=()).model_dump(exclude={"records", "filters"}), records=(extended,), filters=request.filters)
    h.runner.run_once()
    capture = next(iter(h.store.captures.values()))
    assert json.loads(capture.provider_record_json)["source_metadata"] == [["source_fingerprint", "raw-provider-json-hash"]]
    assert json.loads(next(iter(h.store.pages.values())))["filters"] == [["rated", "true"]]


class EncounterReceiver(Receiver):
    """Semantic owner fake; actual introduction transactions belong to #487."""
    def __init__(self):
        super().__init__()
        self.introduced_cards = set()
        self.deferred_reason = None
        self.first_introductions = 0

    def accept_encounter(self, encounter):
        self.calls.append(encounter)
        logical_id = encounter.key.logical_id()
        prior = self.receipts.get(logical_id)
        if isinstance(prior, EncounterReceipt):
            return prior.model_copy(update={"outcome": "duplicate"})
        held = prior is not None and prior.status == "held"
        fresh = encounter.decision.introduction == "unintroduced" and encounter.decision.presentation_id not in self.introduced_cards
        disposition = "first_introduction" if fresh else ("review_priority" if encounter.observation == "missed" else "frequency_only")
        deferred = self.deferred_reason
        frequency_only = disposition == "frequency_only" and not held
        if not held and deferred is None and fresh:
            self.introduced_cards.add(encounter.decision.presentation_id)
            self.first_introductions += 1
        receipt = EncounterReceipt(key=encounter.key, source_revision=encounter.source.source_revision,
            status="held" if held else ("pending" if deferred else "applied"),
            outcome="withdrawn" if held else "accepted", owner_receipt_id="owner-encounter-receipt",
            admitted_at=None, available_by=None, applied_at=None,
            reason="source_held" if held else ("owner_deferred" if deferred else None),
            detected_at=encounter.detected_at, source_digest=encounter.source.source_digest,
            observation=encounter.observation, observation_recorded=not held,
            disposition=None if held else disposition,
            admission_status="pending" if held or deferred else ("not_requested" if frequency_only else "available"),
            deferred_reason=deferred if not held else None)
        self.receipts[logical_id] = receipt
        if self.uncertain:
            self.uncertain = False
            raise TimeoutError("owner_committed_but_ack_lost")
        return receipt

    def encounter_status(self, key):
        receipt = self.receipts.get(key.logical_id())
        return receipt if isinstance(receipt, EncounterReceipt) else None


def encounter_harness(store=None, *, observation="missed"):
    h = harness(store, kinds=("known_unintroduced",))
    h.receiver = EncounterReceiver()
    h.coordinator.learning = h.receiver
    h.encounter_state = SimpleNamespace(introduced=False)
    normalize = h.coordinator.normalizer.normalize
    if observation == "matched":
        h.coordinator.normalizer = SimpleNamespace(normalize=lambda value, request:
            normalize(value, request).model_copy(update={"moves": ["e2e4"]}))

    capture_snapshot = h.coordinator.snapshots.snapshot
    def snapshot(repertoire_id):
        captured = capture_snapshot(repertoire_id)
        mapping = captured.decision_presentations[0].model_copy(update={
            "introduction": "introduced" if h.encounter_state.introduced else "unintroduced",
            "introduced_at": "2026-10-26T00:00:00Z" if h.encounter_state.introduced else None,
            "introduced_before_game": None, "introduction_game": None})
        return captured.model_copy(update={"decision_presentations": (mapping,)})
    h.coordinator.snapshots = SimpleNamespace(snapshot=snapshot)
    if observation == "matched":
        compare = h.coordinator.comparer.compare
        h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs:
            tuple(item.model_copy(update={"played_uci": "e2e4"}) for item in compare(*args, **kwargs)))
    h.coordinator.encounter_feedback = True
    return h


@pytest.mark.parametrize("observation", ["matched", "missed"])
def test_unintroduced_encounter_delivery_is_not_a_lapse(observation):
    h = encounter_harness(observation=observation)
    h.store.enqueue(spec())
    h.runner.run_once()
    if observation == "matched":
        # #618/#619: covered unseen encounters remain facts without urgency.
        assert not h.store.deliveries and not h.receiver.calls
        captured, = h.store.captures.values()
        assert captured.first_departure.status == "covered" and captured.encounters == ()
        assert captured.findings[0].kind == "known_unintroduced"
        return
    delivery = next(iter(h.store.deliveries.values()))
    assert delivery.command_kind == "encounter" and delivery.lapse is None
    assert delivery.encounter.observation == observation
    assert next(iter(h.store.captures.values())).encounters[0] == delivery.encounter
    h.runner.run_once()
    assert h.receiver.first_introductions == 1
    assert not any(isinstance(command, GameLapse) for command in h.receiver.calls)


@pytest.mark.parametrize("lose_ack", [False, True])
def test_confirmed_introduced_miss_uses_immutable_pending_intake_without_lapse_or_queue(lose_ack):
    """A genuine miss remains a fact; acceptance does not require Study refresh."""
    h = harness()
    h.coordinator.encounter_feedback = True
    calls, accepted = [], {}
    def accept_encounter(command):
        nonlocal lose_ack
        calls.append(command)
        key = command.key.logical_id()
        if key in accepted:
            original, receipt = accepted[key]
            assert command == original
            return receipt.model_copy(update={"outcome": "duplicate"})
        receipt = EncounterReceipt(key=command.key, source_revision=command.source.source_revision,
            status="pending", outcome="accepted", owner_receipt_id="public-intake-receipt",
            admitted_at=None, available_by=None, applied_at=None, reason="priority_refresh_pending",
            detected_at=command.detected_at, source_digest=command.source.source_digest,
            observation=command.observation, observation_recorded=True,
            disposition=None, admission_status="pending", deferred_reason=None)
        accepted[key] = command, receipt
        if lose_ack:
            lose_ack = False
            raise TimeoutError("accepted_but_ack_lost")
        return receipt
    # No lapse, scheduling, queue, refresh or availability operation is supplied.
    h.coordinator.learning = SimpleNamespace(accept_encounter=accept_encounter)
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original, = h.store.deliveries.values()
    command = original.encounter
    assert isinstance(command, GameEncounter) and original.lapse is None
    captured, = h.store.captures.values()
    finding, = captured.findings
    assert finding.kind == "confirmed_miss" and finding.lapse_eligible
    assert command.decision.introduction == "introduced"
    mapping = h.coordinator.snapshots.snapshot("white").decision_presentations[0]
    assert command.decision == mapping.model_copy(update={"route_id": "route-a", "route_ply": 0})
    assert command.decision.introduction_matches_current_content
    assert command.decision.introduced_at < command.occurred_at
    assert command.observation == "missed" and command.played_uci == finding.played_uci == "d2d4"
    assert command.expected_uci == finding.expected_uci[0] == "e2e4"
    assert command.key.decision_id == finding.decision_id and command.key.occurrence_ply == finding.ply
    assert command.source.source_digest == finding.source_digest == captured.record.source_digest
    assert command.occurred_at == captured.record.played_at
    assert h.runner.run_once() == "delivery"
    delivered = h.store.delivery(original.operation_id)
    if delivered.state == "pending":
        h.clock.value += 10
        assert FeedbackRunner(h.coordinator).run_once() == "delivery"
        delivered = h.store.delivery(original.operation_id)
        assert delivered.receipt.outcome == "duplicate" and calls == [command, command]
    else:
        assert calls == [command] and delivered.receipt.outcome == "accepted"
    assert delivered.state == "acknowledged" and delivered.encounter == command
    assert delivered.receipt.status == delivered.receipt.admission_status == "pending"
    assert delivered.receipt.disposition is delivered.receipt.available_by is delivered.receipt.applied_at is None
    # Later owner progress/missing comparison proof cannot manufacture a new event.
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs: ())
    h.store.enqueue(spec("later-owner-progress"))
    h.coordinator.capture_page(h.store.load_job("later-owner-progress"))
    assert len(h.store.deliveries) == len(accepted) == 1
    assert next(iter(h.store.sources.values())).effects == (command,)
    assert FeedbackRunner(h.coordinator).run_once() == "idle"


def test_encounter_mode_does_not_relabel_or_duplicate_frozen_legacy_lapse():
    h = harness()
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original, = h.store.deliveries.values()
    assert isinstance(original.lapse, GameLapse)
    h.coordinator.encounter_feedback = True
    h.store.enqueue(spec("encounter-recomparison"))
    h.coordinator.capture_page(h.store.load_job("encounter-recomparison"))
    assert tuple(h.store.deliveries.values()) == (original,)
    assert next(iter(h.store.sources.values())).effects == (original.lapse,)


@pytest.mark.parametrize("later_finding", ["confirmed_miss", "unavailable"])
def test_encounter_lost_ack_and_later_introduction_keep_original_effect(later_finding):
    h = encounter_harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original = next(iter(h.store.deliveries.values()))
    h.receiver.uncertain = True
    h.runner.run_once()
    assert h.receiver.first_introductions == 1 and h.store.delivery(original.operation_id).state == "pending"
    h.clock.value += 10
    FeedbackRunner(h.coordinator).run_once()
    assert h.store.delivery(original.operation_id).receipt.outcome == "duplicate"
    h.encounter_state.introduced = True
    h.coordinator.comparer = (harness(kinds=("confirmed_miss",)).coordinator.comparer
        if later_finding == "confirmed_miss" else SimpleNamespace(compare=lambda *_, **__: ()))
    h.store.enqueue(spec("after-introduction"))
    FeedbackRunner(h.coordinator).run_once()
    assert len(h.store.deliveries) == 1 and h.receiver.first_introductions == 1
    source = next(iter(h.store.sources.values()))
    assert not source.quarantined and source.effects == (original.encounter,)
    assert source.effects[0].detected_at == original.encounter.detected_at
    assert h.store.delivery(original.operation_id).encounter == original.encounter


def test_corrected_encounter_is_held_before_admission_and_delayed_retry():
    h = encounter_harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original = next(iter(h.store.deliveries.values()))
    h.page[0] = page(records=(record(2, "corrected"),))
    h.store.enqueue(spec("corrected-encounter"))
    FeedbackRunner(h.coordinator).run_once()
    assert h.store.delivery(original.operation_id).state == "superseded"
    h.coordinator.deliver(h.store.pending_delivery(h.clock.now()))
    restarted = EncounterReceiver()
    restarted.receipts = dict(h.receiver.receipts)
    assert restarted.accept_encounter(original.encounter).status == "held"
    assert restarted.first_introductions == 0


@pytest.mark.parametrize("reason", ["paused", "active_attempt"])
def test_encounter_owner_deferral_receipt_is_preserved(reason):
    h = encounter_harness()
    h.receiver.deferred_reason = reason
    h.store.enqueue(spec())
    h.runner.run_once()
    h.runner.run_once()
    delivery = next(iter(h.store.deliveries.values()))
    # Serialize/reopen the producer record: no subtype/disposition fields may vanish.
    restored = type(delivery).model_validate_json(delivery.model_dump_json())
    assert restored.state == "acknowledged" and isinstance(restored.receipt, EncounterReceipt)
    assert restored.receipt.admission_status == "pending" and restored.receipt.deferred_reason == reason
    assert restored.receipt.available_by is None and h.receiver.first_introductions == 0
    assert h.store.pending_delivery(h.clock.now()) is None  # Owner now owns pending admission.


def test_historical_encounter_preserves_importance_without_lapse():
    h = encounter_harness()
    h.encounter_state.introduced = True
    h.store.enqueue(spec())
    h.runner.run_once()
    h.runner.run_once()
    delivery = next(iter(h.store.deliveries.values()))
    assert delivery.receipt.disposition == "review_priority"
    assert delivery.encounter.observation == "missed" and delivery.lapse is None
    assert h.receiver.first_introductions == 0


def test_distinct_encounters_keep_provenance_without_reintroducing_one_card():
    h = encounter_harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    h.runner.run_once()
    h.encounter_state.introduced = True
    h.page[0] = page(records=(record().model_copy(update={"source_game_id": "another-game"}),))
    h.store.enqueue(spec("another-encounter"))
    FeedbackRunner(h.coordinator).run_once()
    pending = h.store.pending_delivery(h.clock.now())
    h.coordinator.deliver(pending)
    assert h.receiver.first_introductions == 1
    assert len(h.store.deliveries) == 2 and len(h.store.captures) == 2
    assert {item.encounter.key.game_id for item in h.store.deliveries.values()} == {"provider-game", "another-game"}
    assert pending.encounter.observation == "missed" and h.store.delivery(pending.operation_id).receipt.disposition == "review_priority"


@pytest.mark.parametrize("ordinary_limit,ordinary_used,study_only", [
    (0, 0, False), (4, 4, False), (0, 0, True), (4, 4, True),
])
def test_encounter_transport_does_not_apply_ordinary_admission_controls(ordinary_limit, ordinary_used, study_only):
    """The owner fake grants the exception; Games only dispatches and preserves it.

    Actual Study cap/Study-only/canonical writer proofs stay with #487/#495.
    """
    h = encounter_harness()
    controls = {"ordinary_limit": ordinary_limit, "ordinary_used": ordinary_used, "study_only": study_only}
    original_controls = dict(controls)
    h.receiver.ordinary_controls = controls
    h.store.enqueue(spec())
    h.runner.run_once()
    h.runner.run_once()
    delivery = next(iter(h.store.deliveries.values()))
    assert delivery.receipt.disposition == "first_introduction"
    assert delivery.receipt.admission_status == "available" and h.receiver.first_introductions == 1
    assert delivery.lapse is None and controls == original_controls
    assert h.receiver.ordinary_controls is controls


def test_encounter_transport_accepts_racing_ordinary_introduction_receipt():
    """An owner may observe ordinary admission after the producer captured facts."""
    h = encounter_harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original = next(iter(h.store.deliveries.values()))
    h.receiver.introduced_cards.add(original.encounter.decision.presentation_id)
    h.runner.run_once()
    delivery = h.store.delivery(original.operation_id)
    assert delivery.receipt.disposition == "review_priority" and delivery.receipt.admission_status == "available"
    assert h.receiver.first_introductions == 0 and delivery.encounter == original.encounter
    assert not any(isinstance(command, GameLapse) for command in h.receiver.calls)


def test_canonical_history_projection_needs_no_pgn_or_acquisition():
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(json.loads((FIXTURES / "snapshot.json").read_text())["payload"]))
    mapping = snapshot.decision_presentations[0].model_copy(update={"introduction": "unintroduced",
        "introduced_at": None, "introduced_before_game": None, "introduction_game": None})
    snapshot = snapshot.model_copy(update={"decision_presentations": (mapping,)})
    game = CanonicalGame(workspace_id="fixture", game_id="retained-history", game_revision=1,
        source_id="retained-observed-source", source_revision=3, source_digest="a" * 64,
        starting_fen=snapshot.membership.routes[0].starting_fen, moves=["d2d4"],
        learner_color="white", played_at=None)
    source = IntroductionGameScope(provider="lichess", game_id=game.game_id,
        source_revision=game.source_revision, source_digest=game.source_digest)
    payload = json.loads((FIXTURES / "unintroduced.json").read_text())["payload"]
    payload.update(game_id=game.game_id, source_revision=game.source_revision, source_digest=game.source_digest)
    finding = Finding.model_validate_json(json.dumps(payload))
    detected_at = "2026-10-26T12:00:00Z"
    result = project_encounters(game, findings=(finding,), snapshots=(snapshot,), source=source,
        detected_at=detected_at, occurred_at=None, payload_digest="b" * 64)
    assert len(result) == 1
    encounter = result[0]
    assert encounter.key.game_id == game.game_id and encounter.source == source
    assert encounter.decision == mapping.model_copy(update={"route_id": "route-a", "route_ply": 0})
    assert encounter.detected_at == detected_at and encounter.occurred_at is None
    assert encounter.payload_digest == "b" * 64 and encounter.observation == "missed"
    assert (encounter.expected_uci, encounter.played_uci) == ("e2e4", "d2d4")
    with pytest.raises(ValueError, match="source differs"):
        project_encounters(game, findings=(finding,), snapshots=(snapshot,),
            source=source.model_copy(update={"source_digest": "c" * 64}), detected_at=detected_at, occurred_at=None)


@pytest.mark.parametrize("learner_color", ["white", "black"])
def test_capture_selects_every_same_color_snapshot(learner_color):
    h = harness(kinds=())
    original = h.coordinator.snapshots.snapshot("white")
    def supplied(repertoire):
        color = "black" if repertoire == "black" else "white"
        return original.model_copy(update={"learner_color": color,
            "membership": original.membership.model_copy(update={"scope_repertoire_ids": (repertoire,),
                "routes": tuple(route.model_copy(update={"repertoire_id": repertoire, "learner_color": color})
                                for route in original.membership.routes)}),
            "decision_presentations": tuple(mapping.model_copy(update={"repertoire_id": repertoire})
                                             for mapping in original.decision_presentations)})
    h.coordinator.snapshots = SimpleNamespace(snapshot=supplied)
    normalize = h.coordinator.normalizer.normalize
    h.coordinator.normalizer = SimpleNamespace(normalize=lambda record, spec:
        normalize(record, spec).model_copy(update={"learner_color": learner_color}))
    compared = []
    def compare(game, snapshot, **context):
        assert game.learner_color == snapshot.learner_color
        compared.append(snapshot.membership.scope_repertoire_ids[0])
        return ()
    h.coordinator.comparer = SimpleNamespace(compare=compare)
    h.store.enqueue(spec().model_copy(update={"repertoire_ids": ("white", "black", "other-white")}))
    h.runner.run_once()
    assert compared == (["white", "other-white"] if learner_color == "white" else ["black"])
    assert h.store.load_job("job").window_complete


@pytest.mark.parametrize("stage", ["snapshot", "comparison"])
def test_unavailable_comparison_retains_valid_source_and_advances_acquisition(stage):
    h = harness()
    def unavailable(*args, **kwargs):
        raise ValueError("repertoire_publication_unavailable")
    if stage == "snapshot":
        h.coordinator.snapshots = SimpleNamespace(snapshot=unavailable)
    else:
        h.coordinator.comparer = SimpleNamespace(compare=unavailable)
    h.store.enqueue(spec())
    h.runner.run_once()
    source, = h.store.sources.values()
    capture, = h.store.captures.values()
    assert source.normalized is not None and not source.quarantined
    assert source.diagnostic == capture.diagnostic == 'comparison_unavailable:["white"]'
    job = h.store.load_job("job")
    assert job.window_complete and job.status == "complete" and job.error == "comparison_unavailable"
    assert not h.store.deliveries and source.findings == ()


def test_unavailable_recomparison_does_not_hold_original_lapse():
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original, = h.store.deliveries.values()
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    rescan = h.store.enqueue(spec("rescan"))
    h.coordinator.capture_page(rescan)
    source, = h.store.sources.values()
    assert not source.quarantined and source.effects == (original.lapse,)
    assert list(h.store.deliveries.values()) == [original]


@pytest.mark.parametrize("provider,reason", [
    ("lichess", "filtered_speed"), ("lichess", "filtered_rated"),
    ("lichess", "unsupported_variant"), ("lichess", "duplicate_source_id"),
    ("chesscom", "outside_window"), ("chesscom", "time_control_filtered"),
    ("chesscom", "rated_filtered"), ("chesscom", "unsupported_variant"),
    ("chesscom", "duplicate_source_id"),
])
def test_known_provider_exclusions_advance_checkpoint_with_evidence(provider, reason):
    h = harness(kinds=())
    request = spec(provider=provider)
    rejection = GameRejection(source_game_id="excluded", source_revision=1, reason=reason)
    h.page[0] = page(request=request, rejections=(rejection,))
    h.store.enqueue(request)
    h.runner.run_once()
    job = h.store.load_job("job")
    assert job.window_complete and job.status == "complete" and job.rejections == (rejection,)
    assert json.loads(next(iter(h.store.pages.values())))["rejections"][0]["reason"] == reason


@pytest.mark.parametrize("reason", ["malformed_record", "unknown_reason", "conflicting_source_id", "invalid_pgn", "outside_window"])
def test_unsafe_or_wrong_provider_rejection_does_not_complete_acquisition(reason):
    h = harness(kinds=())
    rejection = GameRejection(source_game_id="unsafe", source_revision=1, reason=reason)
    h.page[0] = page(rejections=(rejection,))
    h.store.enqueue(spec())
    h.runner.run_once()
    job = h.store.load_job("job")
    assert not job.window_complete and job.status == "quarantined" and job.last_checkpoint_at is None
    assert job.rejections == (rejection,)


def test_capture_time_only_rescan_reuses_comparison_capture_and_original_delivery():
    h = harness()
    captured = h.coordinator.snapshots.snapshot("white")
    h.store.enqueue(spec())
    h.runner.run_once()
    original, = h.store.deliveries.values()
    comparison = next(iter(h.store.sources.values())).comparison_revision
    later = captured.model_copy(update={"membership": captured.membership.model_copy(update={
        "captured_at": "2026-10-29T20:00:00Z"})})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: later)
    h.clock.value += 100
    rescan = h.store.enqueue(spec("rescan"))
    h.coordinator.capture_page(rescan)
    assert len(h.store.captures) == len(h.store.deliveries) == 1
    assert next(iter(h.store.sources.values())).comparison_revision == comparison
    assert list(h.store.deliveries.values()) == [original]


def test_material_snapshot_change_preserves_comparison_history_without_duplicate_effect():
    h = harness()
    captured = h.coordinator.snapshots.snapshot("white")
    h.store.enqueue(spec())
    h.runner.run_once()
    later = captured.model_copy(update={"decision_presentations": tuple(mapping.model_copy(update={
        "presentation_revision": mapping.presentation_revision + 1}) for mapping in captured.decision_presentations)})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: later)
    rescan = h.store.enqueue(spec("rescan"))
    h.coordinator.capture_page(rescan)
    assert len(h.store.captures) == 2 and len(h.store.deliveries) == 1


def test_comparer_returning_wrong_source_is_an_integrity_quarantine():
    h = harness()
    compare = h.coordinator.comparer.compare
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs: tuple(
        finding.model_copy(update={"source_digest": "f" * 64}) for finding in compare(*args, **kwargs)))
    h.store.enqueue(spec())
    h.runner.run_once()
    source, = h.store.sources.values()
    assert source.quarantined and source.normalized is not None
    assert not h.store.load_job("job").window_complete and not h.store.deliveries



def test_real_comparer_captures_both_colors_with_both_repertoires_selected():
    from app.games.feedback.comparison import compare_game, normalize_record
    from test_product_game_findings import record as pgn_record, route, snapshot
    from tempo_game_review_contracts import replay_line
    white = snapshot(route(("e2e4",), repertoire_id="white"))
    black_route = route(("e2e4", "e7e5"), repertoire_id="black").model_copy(update={"learner_color": "black"})
    black = snapshot(black_route)
    black_mapping = white.decision_presentations[0].model_copy(update={
        "repertoire_id": "black", "decision_id": "black-decision", "presentation_id": "black-card",
        "position_key": replay_line(black_route.starting_fen, black_route.moves)[1]["comparison_key"],
        "selected_response_uci": "e7e5"})
    black = black.model_copy(update={"learner_color": "black", "decision_presentations": (black_mapping,)})
    white_record = pgn_record(("d2d4",)).model_copy(update={"source_game_id": "white-game"})
    black_record = pgn_record(("e2e4", "c7c5"))
    black_pgn = black_record.pgn.replace('White "Learner"', 'White "Other"').replace('Black "Other"', 'Black "Learner"')
    black_record = black_record.model_copy(update={"pgn": black_pgn,
        "source_digest": hashlib.sha256(black_pgn.encode()).hexdigest(), "source_game_id": "black-game"})
    h = harness()
    request = spec().model_copy(update={"workspace_id": "workspace", "repertoire_ids": ("white", "black")})
    h.page[0] = page(request=request, records=(white_record, black_record))
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda repertoire: {"white": white, "black": black}[repertoire])
    h.coordinator.normalizer = SimpleNamespace(normalize=lambda value, spec: normalize_record(value,
        provider=spec.provider, account=spec.account, workspace_id=spec.workspace_id))
    h.coordinator.comparer = SimpleNamespace(compare=lambda game, snapshot, record, spec:
        compare_game(game, provider=spec.provider, snapshot=snapshot, time_precision=record.time_precision))
    h.store.enqueue(request)
    h.runner.run_once()
    assert h.store.load_job("job").window_complete
    sources = sorted(h.store.sources.values(), key=lambda source: source.normalized.learner_color)
    assert [(source.normalized.learner_color, source.findings[0].ply, source.findings[0].repertoire_id)
            for source in sources] == [("black", 2, "black"), ("white", 1, "white")]
    assert all(not source.quarantined and source.findings[0].kind == "confirmed_miss" for source in sources)


def test_partial_comparison_failure_keeps_successful_repertoire_findings():
    h = harness()
    supplied = h.coordinator.snapshots.snapshot
    def snapshot(repertoire):
        if repertoire == "unavailable":
            raise TimeoutError()
        return supplied(repertoire)
    h.coordinator.snapshots = SimpleNamespace(snapshot=snapshot)
    h.store.enqueue(spec().model_copy(update={"repertoire_ids": ("white", "unavailable")}))
    h.runner.run_once()
    source, = h.store.sources.values()
    assert not source.quarantined and len(source.findings) == 1 and source.effects == ()
    assert source.first_departure.status == "unresolved"
    assert source.diagnostic == 'comparison_unavailable:["unavailable"]'
    assert h.store.load_job("job").window_complete


def test_no_compatible_snapshot_is_unresolved_instead_of_all_correct():
    h = harness(kinds=())
    snapshot = h.coordinator.snapshots.snapshot("white").model_copy(update={"learner_color": "black"})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: snapshot)
    h.store.enqueue(spec())
    h.runner.run_once()
    source, = h.store.sources.values()
    assert source.normalized is not None and not source.quarantined
    assert source.diagnostic == "no_compatible_repertoire" and source.findings == ()
    assert h.store.load_job("job").window_complete


def test_incomplete_page_with_repeated_continuation_keeps_old_checkpoint():
    h = harness(kinds=())
    job = h.store.enqueue(spec())
    job = job.model_copy(update={"continuation": "same-cursor"})
    h.store.save_job(job)
    h.page[0] = page(continuation="same-cursor", complete=False)
    h.coordinator.capture_page(h.store.load_job("job"))
    saved = h.store.load_job("job")
    assert saved.status == "quarantined" and not saved.window_complete
    assert saved.continuation == "same-cursor" and saved.last_checkpoint_at is None



def test_mutated_conflicting_page_identity_never_commits_a_false_checkpoint():
    h = harness(kinds=())
    h.page[0] = page().model_copy(update={"records": (record(), record(text="conflict"))})
    h.store.enqueue(spec())
    h.runner.run_once()
    assert not h.store.sources and not h.store.captures
    job = h.store.load_job("job")
    assert not job.window_complete and job.last_checkpoint_at is None


def test_snapshot_scope_corruption_is_quarantined_without_losing_acquired_game():
    h = harness(kinds=())
    captured = h.coordinator.snapshots.snapshot("white")
    captured = captured.model_copy(update={"membership": captured.membership.model_copy(update={"workspace_id": "wrong"})})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: captured)
    h.store.enqueue(spec())
    h.runner.run_once()
    source, = h.store.sources.values()
    assert source.normalized is not None and source.quarantined
    assert source.diagnostic == "unresolved_parsing_or_comparison_boundary"
    assert not h.store.load_job("job").window_complete


def test_comparison_identity_ignores_unordered_snapshot_row_order():
    from app.games.feedback.coordinator import _comparison_revision
    from test_product_game_findings import route, snapshot
    captured = snapshot(route(route_id="first"), route(route_id="second"))
    reversed_snapshot = captured.model_copy(update={
        "membership": captured.membership.model_copy(update={"routes": tuple(reversed(captured.membership.routes))}),
        "boundaries": tuple(reversed(captured.boundaries)),
        "decision_presentations": tuple(reversed(captured.decision_presentations))})
    assert _comparison_revision((captured,), ()) == _comparison_revision((reversed_snapshot,), ())



def test_source_correction_during_comparison_outage_still_fences_original():
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    original, = h.store.deliveries.values()
    h.page[0] = page(records=(record(revision=2, text="corrected"),))
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    rescan = h.store.enqueue(spec("rescan"))
    h.coordinator.capture_page(rescan)
    source, = h.store.sources.values()
    assert source.quarantined and source.diagnostic == "changed_source_requires_D08_resolution"
    assert source.original == record() and source.effects == (original.lapse,)
    assert len(h.store.captures) == 2
    controls = [delivery.control for delivery in h.store.deliveries.values() if delivery.control]
    assert len(controls) == 1 and controls[0].reason == "source_corrected"
    assert controls[0].key == original.lapse.key


@pytest.mark.parametrize("boundary", ["opponent", "learner", "incomplete"])
def test_capture_retains_global_selection_without_per_repertoire_false_effect(boundary):
    from app.games.feedback.comparison import compare_game
    from test_product_first_boundary import snapshot
    h = harness()
    captured = {"e4": snapshot(repertoire="e4", introduction="unintroduced"),
        "d4": snapshot(("d2d4", "d7d5"), repertoire="d4", route_id="d4", introduction="unintroduced")}
    moves = ["e2e4", "c7c5"] if boundary == "opponent" else ["e2e4", "e7e5", "d2d4"]
    normalize = h.coordinator.normalizer.normalize
    h.coordinator.normalizer = SimpleNamespace(normalize=lambda original, request:
        normalize(original, request).model_copy(update={"moves": moves}))
    def supplied(repertoire):
        if repertoire == "unavailable":
            raise TimeoutError()
        return captured[repertoire]
    h.coordinator.snapshots = SimpleNamespace(snapshot=supplied)
    h.coordinator.comparer = SimpleNamespace(compare=lambda current, value, **kwargs:
        compare_game(current, provider="lichess", snapshot=value, time_precision=kwargs["record"].time_precision))
    h.coordinator.encounter_feedback = True
    request = spec().model_copy(update={"repertoire_ids": ("e4", "d4") + (("unavailable",) if boundary == "incomplete" else ())})
    h.store.enqueue(request)
    assert h.runner.run_once() == "page"
    source, = h.store.sources.values()
    capture, = h.store.captures.values()
    assert not source.quarantined and source.first_departure == capture.first_departure
    assert any(item.repertoire_id == "d4" and item.ply == 1 for item in source.findings)
    if boundary == "learner":
        assert (source.first_departure.status, source.first_departure.ply) == ("own_actionable", 3)
        command, = source.encounters
        assert command.key.occurrence_ply == 3 and command.decision.presentation_id == "card-route"
        assert len(h.store.deliveries) == 1 and source.determined_at == capture.determined_at == h.clock.utc_now()
    else:
        assert source.first_departure.status == ("opponent_gap" if boundary == "opponent" else "unresolved")
        assert not h.store.deliveries and not source.encounters and source.determined_at is None


def test_old_source_and_capture_json_keep_absent_selection_explicit():
    from app.games.feedback.ports import Capture, SourceState
    h = harness()
    h.store.enqueue(spec())
    h.runner.run_once()
    source, = h.store.sources.values()
    capture, = h.store.captures.values()
    for model, value in ((SourceState, source), (Capture, capture)):
        historical = value.model_dump_json(exclude={"first_departure", "determined_at"})
        restored = model.model_validate_json(historical)
        assert restored.first_departure is restored.determined_at is None


@pytest.mark.parametrize("encounter_mode", [False, True])
def test_new_commands_in_either_envelope_respect_opponent_first_boundary(encounter_mode):
    from test_product_game_findings import FakeReentryIndex, record as real_record, route, snapshot
    from app.games.feedback.comparison import compare_record, normalize_record
    authored = route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4)
    captured = snapshot(authored)
    original = real_record(("g1f3", "c7c5", "g2g3", "d7d5", "f1h3"))
    current = normalize_record(original, provider="lichess", account="Learner", workspace_id="workspace")
    findings = compare_record(original, provider="lichess", account="Learner", workspace_id="workspace",
        snapshot=captured, reentry_lookup=FakeReentryIndex(captured, 4))
    assert [(item.kind, item.ply) for item in findings] == [("opponent_gap", 2), ("confirmed_miss", 5)]
    h = harness()
    h.coordinator.encounter_feedback = encounter_mode
    h.coordinator.normalizer = SimpleNamespace(normalize=lambda *args: current)
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args, **kwargs: findings)
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: captured)
    request = spec().model_copy(update={"workspace_id": "workspace", "repertoire_ids": ("rep",)})
    h.page[0] = page(request, records=(original,))
    h.store.enqueue(request)
    assert h.runner.run_once() == "page"
    source, = h.store.sources.values()
    assert source.first_departure.status == "opponent_gap" and source.first_departure.ply == 2
    assert source.findings == findings and source.effects == () and not h.store.deliveries


@pytest.mark.parametrize("original_observation", ["matched", "missed"])
def test_corrected_held_source_cannot_create_or_inherit_determination_clock(original_observation):
    h = encounter_harness(observation=original_observation)
    h.store.enqueue(spec())
    h.runner.run_once()
    original, = h.store.sources.values()
    assert (original.determined_at is None) == (original_observation == "matched")
    h.clock.value += 60
    h.page[0] = page(records=(record(2, "corrected"),))
    h.coordinator.normalizer = harness().coordinator.normalizer
    h.coordinator.comparer = harness(kinds=("known_unintroduced",)).coordinator.comparer
    correction = spec("corrected-determination")
    h.store.enqueue(correction)
    h.coordinator.capture_page(h.store.load_job(correction.job_id))
    source, = h.store.sources.values()
    changed_capture = list(h.store.captures.values())[-1]
    assert source.quarantined and source.first_departure == original.first_departure
    assert source.determined_at == original.determined_at
    assert changed_capture.first_departure.status == "own_actionable"
    assert changed_capture.determined_at is None
    assert changed_capture.diagnostic == "changed_source_requires_D08_resolution"
