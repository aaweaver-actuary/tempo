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
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.games.feedback.coordinator import Coordinator, delivery_for, stable_id
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
            game_revision=value.source_revision, source_id=value.source_game_id,
            source_revision=value.source_revision, source_digest=value.source_digest,
            starting_fen="start", moves=["d2d4"], learner_color="white", played_at=value.played_at)
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
    h.page[0] = ProviderPage(**page(records=()).model_dump(exclude={"records"}), records=(extended,), filters=request.filters)
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
    delivery = next(iter(h.store.deliveries.values()))
    assert delivery.command_kind == "encounter" and delivery.lapse is None
    assert delivery.encounter.observation == observation
    assert next(iter(h.store.captures.values())).encounters[0] == delivery.encounter
    h.runner.run_once()
    assert h.receiver.first_introductions == 1
    assert not any(isinstance(command, GameLapse) for command in h.receiver.calls)


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


@pytest.mark.parametrize("reason", ["paused", "introduction_allowance"])
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
