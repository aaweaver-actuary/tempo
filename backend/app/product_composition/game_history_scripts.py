"""Finite historical acquisition and offline first-departure operator composition.

No work on import. The manifest freezes saved scope, not credentials or PGNs.
Database jobs/pages own progress; the local file is never a checkpoint writer.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
from threading import Event
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.contracts.product_v02 import Identifier, Value
from app.contracts.product_v02.games import GameWindow
from app.games.feedback.interfaces import Coordinator, FeedbackRunner, stable_id
from app.games.feedback.review import SyncConfiguration
from .game_sync import WorkflowComparer, WorkflowNormalizer, create_game_sync, sync_job


def configuration_policy(configuration):
    from .game_review import configuration_policy as existing_policy
    return existing_policy(configuration)


class HistoryManifest(Value):
    schema_version: Literal[1] = 1
    run_id: Identifier
    workspace_id: Identifier
    window: GameWindow
    configurations: tuple[SyncConfiguration, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def scope(self):
        if (len({c.provider for c in self.configurations}) != len(self.configurations)
                or any(c.workspace_id != self.workspace_id for c in self.configurations)):
            raise ValueError("historical_manifest_scope_mismatch")
        return self

    def job(self, configuration, *, compare=False):
        spec = sync_job(job_id=stable_id("historical-games-v1", self.run_id,
            "compare" if compare else "collect", configuration.provider),
            workspace_id=self.workspace_id, account=configuration.account,
            window=self.window, repertoire_ids=configuration.repertoire_ids,
            policy=configuration_policy(configuration))
        # Exact-job claims only. History cannot advance a Refresh now checkpoint
        # or be consumed as network acquisition by an ordinary collector.
        return spec.model_copy(update={"purpose": "history", "stream": "manual",
            "configuration_revision": configuration.revision})


def new_manifest(workspace, configurations, *, now=None):
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = end.replace(microsecond=end.microsecond // 1000 * 1000)
    month_index = end.year * 12 + end.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    start = end.replace(year=year, month=month,
        day=min(end.day, calendar.monthrange(year, month)[1]))
    return HistoryManifest(run_id=uuid4().hex, workspace_id=workspace,
        window=GameWindow(start_at=start.isoformat(), end_at=end.isoformat(), precision="millisecond"),
        configurations=tuple(configurations))


def save_manifest(path, manifest):
    payload = manifest.model_dump_json()
    envelope = json.dumps(dict(manifest=json.loads(payload),
        digest=hashlib.sha256(payload.encode()).hexdigest()), indent=2) + "\n"
    # Never overwrite a prior run, including after a failed/partial local write.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(envelope)
        output.flush()
        os.fsync(output.fileno())


def load_manifest(path):
    if Path(path).stat().st_size > 64 * 1024:
        raise ValueError("historical_manifest_exceeds_bound")
    envelope = json.loads(Path(path).read_text())
    manifest = HistoryManifest.model_validate_json(json.dumps(envelope["manifest"]))
    if hashlib.sha256(manifest.model_dump_json().encode()).hexdigest() != envelope["digest"]:
        raise ValueError("historical_manifest_digest_mismatch")
    return manifest


def parse_page(document, provider):
    from app.games.providers.lichess_acquisition import LichessGamePage
    from app.games.providers.chesscom_acquisition import ChesscomGamePage
    return (LichessGamePage if provider == "lichess" else ChesscomGamePage).model_validate_json(document)


class RetainedPages:
    """An acquisition-shaped read capability; never constructs an HTTP client."""
    def __init__(self, store, spec):
        self.store, self.spec = store, spec

    def fetch_page(self, *, account, window, continuation=None):
        if (account, window) != (self.spec.account, self.spec.window):
            raise ValueError("historical_replay_scope_mismatch")
        job = self.store.load_job(self.spec.job_id)
        if job is None or job.spec != self.spec or job.status != "complete" or not job.window_complete:
            raise ValueError("historical_import_not_complete")
        page = parse_page(self.store.retained_page(self.spec.job_id, continuation), self.spec.provider)
        if (page.provider, page.account, page.window, page.filters) != (
                self.spec.provider, account, window, self.spec.filters):
            raise ValueError("historical_retained_page_scope_mismatch")
        return page


class RecoveredFirstPage:
    """Replay the original quarantined page once before provider pagination resumes."""
    def __init__(self, store, spec, underlying):
        self.store, self.spec, self.underlying = store, spec, underlying

    def fetch_page(self, *, account, window, continuation=None):
        if continuation is not None:
            return self.underlying.fetch_page(account=account, window=window, continuation=continuation)
        job = self.store.load_job(self.spec.job_id)
        if (job is None or job.spec != self.spec or job.status != "running"
                or job.error != "history_precision_replay_saved_page"
                or (account, window) != (self.spec.account, self.spec.window)):
            raise ValueError("history_precision_replay_job_mismatch")
        page = parse_page(self.store.retained_page(self.spec.job_id, None), self.spec.provider)
        if (page.provider, page.account, page.window, page.filters) != (
                self.spec.provider, account, window, self.spec.filters):
            raise ValueError("history_precision_replay_scope_mismatch")
        return page


class RecoveredDailyPage:
    """Replay exactly the saved quarantined page, then return to the provider."""
    def __init__(self, store, spec, underlying, cursor):
        self.store, self.spec, self.underlying, self.cursor = store, spec, underlying, cursor

    def fetch_page(self, *, account, window, continuation=None):
        if continuation != self.cursor:
            return self.underlying.fetch_page(account=account, window=window, continuation=continuation)
        job = self.store.load_job(self.spec.job_id)
        if (job is None or job.spec != self.spec or job.status != "running"
                or job.history_replay_cursor != continuation or continuation != job.continuation
                or (account, window) != (self.spec.account, self.spec.window)):
            raise ValueError("history_daily_replay_job_mismatch")
        page = parse_page(self.store.retained_page(self.spec.job_id, continuation), self.spec.provider)
        if (page.provider, page.account, page.window, page.filters) != (
                self.spec.provider, account, window, self.spec.filters):
            raise ValueError("history_daily_replay_scope_mismatch")
        return page


class DeferredSnapshots:
    def snapshot(self, repertoire_id):
        raise ValueError("historical_collection_comparison_deferred")


def retained_records(store, manifest):
    """Walk committed cursor chains, preserving provider-local record metadata."""
    for configuration in manifest.configurations:
        spec = manifest.job(configuration)
        job = store.load_job(spec.job_id)
        if job is None:
            continue
        cursor, seen = None, set()
        for _ in range(job.pages_committed):
            try:
                page = parse_page(store.retained_page(spec.job_id, cursor), spec.provider)
            except ValueError as error:
                # An idempotent saved-page replay can increment the commit
                # counter without adding another distinct page. A missing
                # pending cursor is normal only while that job is incomplete.
                if (str(error) == "historical_page_missing" and cursor is not None
                        and cursor == job.continuation and job.status != "complete"):
                    break
                raise
            if (page.provider, page.account, page.window, page.filters) != (
                    spec.provider, spec.account, spec.window, spec.filters):
                raise ValueError("historical_retained_page_scope_mismatch")
            yield spec, page
            if page.complete or page.continuation is None:
                break
            if page.continuation in seen:
                raise ValueError("historical_retained_cursor_cycle")
            seen.add(page.continuation)
            cursor = page.continuation


def report(store, review, manifest, *, compare=False):
    jobs = [store.load_job(manifest.job(c, compare=compare).job_id) for c in manifest.configurations]
    configuration_matches = all(review.configuration(manifest.workspace_id, c.provider) == c
        for c in manifest.configurations)
    imported, duplicates, exclusions = set(), 0, Counter()
    selections, receipts, deliveries = Counter(), Counter(), Counter()
    admissions, dispositions, deferrals = Counter(), Counter(), Counter()
    unresolved, missing_sources, delivery_failures, suppressed = 0, 0, 0, 0
    for spec, page in retained_records(store, manifest):
        exclusions.update(item.reason for item in page.rejections)
        for record in page.records:
            key = stable_id(spec.workspace_id, spec.provider, record.source_game_id)
            if key in imported:
                duplicates += 1
                continue
            imported.add(key)
            source = store.source(key)
            if source is None or source.normalized is None or source.quarantined:
                missing_sources += 1
            if compare:
                selection = source.first_departure if source else None
                status = selection.status if selection else "unresolved"
                selections[status] += 1
                if source is None or source.quarantined or status in {"unresolved", "own_unmapped"}:
                    unresolved += 1
                for delivery in review.deliveries(key):
                    deliveries[delivery.state] += 1
                    if delivery.qualification_status == "ineligible":
                        suppressed += 1
                    elif delivery.state == "failed":
                        delivery_failures += 1
                    if delivery.receipt is not None:
                        receipts[delivery.receipt.status] += 1
                        receipt = delivery.receipt
                        if hasattr(receipt, "admission_status"):
                            admissions[receipt.admission_status] += 1
                            dispositions[receipt.disposition or "unknown"] += 1
                            if receipt.deferred_reason is not None:
                                deferrals[receipt.deferred_reason] += 1
    complete = all(j is not None and j.status == "complete" and j.window_complete for j in jobs)
    complete = complete and missing_sources == 0 and configuration_matches
    if compare:
        complete = complete and unresolved == 0 and deliveries["pending"] == 0 and delivery_failures == 0
    return dict(run_id=manifest.run_id, phase="comparison" if compare else "collection",
        complete=complete, configuration_matches=configuration_matches,
        window=manifest.window.model_dump(mode="json"),
        unique_games=len(imported), duplicate_records=duplicates, exclusions=dict(exclusions),
        unavailable_or_quarantined_sources=missing_sources, unresolved_games=unresolved,
        selections=dict(selections), delivery_states=dict(deliveries), receipt_statuses=dict(receipts),
        suppressed_deliveries=suppressed,
        receipt_admission_statuses=dict(admissions), receipt_dispositions=dict(dispositions),
        receipt_deferred_reasons=dict(deferrals), study_progress_current=False,
        jobs=[None if j is None else dict(job_id=j.spec.job_id, status=j.status,
            window_complete=j.window_complete, pages_committed=j.pages_committed,
            error=j.error, next_attempt_at=j.next_attempt_at, lease_until=j.lease_until,
            history_recoveries=[item.model_dump(mode="json") for item in j.history_recoveries]) for j in jobs])


@contextmanager
def historical_admission(*, comparison=False):
    # Explicit finite manual work does not require recurring collection enabled.
    from app.services.activity_gate import activity_gate
    from app.services.redis_admission_gate import background_lease, BackgroundAdmissionDeferred
    from app.services.study_priority import control_snapshot
    policy = control_snapshot()
    if (policy.get("available") is not True or policy.get("active") is not False
            or type(policy.get("study_only_mode")) is not bool
            or (not comparison and policy["study_only_mode"])):
        raise BackgroundAdmissionDeferred("Historical processing is waiting for background admission")
    with activity_gate.background_job("historical_games", "operator", yielding=True), background_lease():
        activity_gate.check_background_admission()
        yield


def comparison_runner(*, store, snapshots, intake, clock, spec):
    coordinator = Coordinator(providers={spec.provider: RetainedPages(store, spec)},
        normalizer=WorkflowNormalizer(), comparer=WorkflowComparer(), snapshots=snapshots,
        store=store, learning=intake, clock=clock, source_identity=store,
        qualifier=intake, effect_identity=intake.effect_identity,
        first_boundary_preparation=intake.prepare_first_boundary, encounter_feedback=True)
    # Delivery is explicitly scoped below; never drain another run's outbox.
    return FeedbackRunner(coordinator, delivery_enabled=False)


def run_history(manifest, *, store, review, clock, admission, stopped,
                runner_factory, compare=False, maximum_units=50):
    if not 1 <= maximum_units <= 10000:
        raise ValueError("historical_maximum_units_must_be_1_to_10000")
    if compare:
        imported = report(store, review, manifest)
        if not imported["complete"]:
            raise ValueError("historical_import_not_complete")
        # A prior complete traversal can retain unavailable repertoire evidence.
        # Revisit that same saved set once per invocation when authority returns;
        # command identities, first determination clocks and receipts survive.
        unresolved = report(store, review, manifest, compare=True)["unresolved_games"]
    else:
        unresolved = 0
    runners = []
    units = 0
    try:
        for configuration in manifest.configurations:
            if review.configuration(manifest.workspace_id, configuration.provider) != configuration:
                raise ValueError("historical_saved_configuration_changed")
            spec = manifest.job(configuration, compare=compare)
            runners.append((configuration, spec, runner_factory(configuration)))
        while units < maximum_units and not stopped.is_set():
            progressed = False
            for configuration, spec, runner in runners:
                if stopped.is_set() or units >= maximum_units:
                    break
                with admission():
                    if review.configuration(manifest.workspace_id, configuration.provider) != configuration:
                        raise ValueError("historical_saved_configuration_changed")
                    store.enqueue(spec)
                    prior = store.load_job(spec.job_id)
                    if (not compare and prior.error == "history_precision_replay_saved_page"
                            and not isinstance(runner.coordinator.providers[spec.provider], RecoveredFirstPage)):
                        runner.coordinator.providers[spec.provider] = RecoveredFirstPage(store, spec,
                            runner.coordinator.providers[spec.provider])
                    if (not compare and prior.history_replay_cursor is not None
                            and prior.history_replay_cursor == prior.continuation
                            and not isinstance(runner.coordinator.providers[spec.provider], RecoveredDailyPage)):
                        runner.coordinator.providers[spec.provider] = RecoveredDailyPage(store, spec,
                            runner.coordinator.providers[spec.provider], prior.history_replay_cursor)
                    if unresolved:
                        if prior.status == "complete":
                            store.save_job(prior.model_copy(update={"status": "queued",
                                "continuation": None, "window_complete": False,
                                "pages_committed": 0, "lease_until": 0}))
                    result = runner.run_once(job_id=spec.job_id)
                if result == "page":
                    units += 1
                    progressed = True
            if not progressed:
                break
            # Do not repeatedly restart a completed unresolved job in this run.
            unresolved = 0
        # Only start feedback after the entire saved set has been compared.
        if compare and all((j := store.load_job(spec.job_id)) is not None
                and j.status == "complete" and j.window_complete for _, spec, _ in runners):
            seen, pending = set(), []
            for import_spec, page in retained_records(store, manifest):
                runner = next(r for _, spec, r in runners if spec.provider == import_spec.provider)
                for record in page.records:
                    key = stable_id(import_spec.workspace_id, import_spec.provider, record.source_game_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    for delivery in review.deliveries(key):
                        if delivery.state != "pending" or delivery.next_attempt_at > clock.now():
                            continue
                        pending.append((delivery, runner, import_spec.provider))
            pending.sort(key=lambda item: (item[0].control is None,
                item[0].determined_at or "", item[0].operation_id))
            for delivery, runner, provider in pending:
                if units >= maximum_units or stopped.is_set():
                    break
                with admission():
                    configuration = next(c for c in manifest.configurations if c.provider == provider)
                    if review.configuration(manifest.workspace_id, provider) != configuration:
                        raise ValueError("historical_saved_configuration_changed")
                    # Leave earlier live urgent work to its owning worker.
                    head = store.pending_delivery(clock.now(), workspace=manifest.workspace_id,
                        priority_order=True)
                    if head is not None and head.control is None and head.stream == "forward":
                        return units
                    runner.coordinator.deliver(delivery)
                units += 1
        return units
    finally:
        for _, _, runner in runners:
            runner.close()


def recover_legacy_precision(manifest, *, store, review, admission, stopped):
    configuration = next((c for c in manifest.configurations if c.provider == "lichess"), None)
    if configuration is None or review.configuration(manifest.workspace_id, "lichess") != configuration:
        raise ValueError("history_precision_saved_scope_changed")
    spec = manifest.job(configuration)
    job = store.load_job(spec.job_id)
    if (job is None or job.spec != spec or job.status != "quarantined"
            or job.error != "unresolved_page_boundary" or job.continuation is not None
            or job.last_checkpoint_at is not None or job.pages_committed != 1 or job.rejections):
        raise ValueError("history_precision_job_not_initial_quarantine")
    page = parse_page(store.retained_page(spec.job_id, None), spec.provider)
    if (page.provider, page.account, page.window, page.filters) != (
            spec.provider, spec.account, spec.window, spec.filters):
        raise ValueError("history_precision_original_page_mismatch")
    recovered = 0
    for record in page.records:
        if stopped.is_set():
            return dict(recovered=recovered, interrupted=True, prepared=False)
        key = stable_id(spec.workspace_id, spec.provider, record.source_game_id)
        source = store.source(key)
        if source is None:
            raise ValueError("history_precision_page_sources_missing")
        if not source.quarantined:
            continue
        verified = WorkflowNormalizer().normalize(record, spec)
        with admission():
            if review.configuration(manifest.workspace_id, "lichess") != configuration:
                raise ValueError("history_precision_saved_scope_changed")
            store.recover_initial_history_precision(spec=spec, record=record, verified=verified,
                configuration=configuration)
        recovered += 1
    with admission():
        if review.configuration(manifest.workspace_id, "lichess") != configuration:
            raise ValueError("history_precision_saved_scope_changed")
        count = store.resume_initial_history_page(spec=spec, page_document=page.model_dump_json(),
            configuration=configuration)
    return dict(recovered=recovered, page_records=count, prepared=True, interrupted=False)


def retry_lichess_provider_limit(manifest, *, store, review, admission, clock):
    configuration = next((c for c in manifest.configurations if c.provider == "lichess"),None)
    if configuration is None or review.configuration(manifest.workspace_id,"lichess") != configuration:
        raise ValueError("history_provider_limit_saved_scope_changed")
    spec = manifest.job(configuration)
    job = store.load_job(spec.job_id)
    if job is None:
        raise ValueError("history_provider_limit_failure_required")
    with admission():
        if review.configuration(manifest.workspace_id,"lichess") != configuration:
            raise ValueError("history_provider_limit_saved_scope_changed")
        receipt = store.resume_history_provider_limit(expected=job, configuration=configuration,
            recorded_at=clock.utc_now())
    return dict(prepared=True, receipt=receipt.model_dump(mode="json"))


def recover_chesscom_daily(manifest, *, store, review, admission):
    configuration = next((c for c in manifest.configurations if c.provider == "chesscom"), None)
    if configuration is None or review.configuration(manifest.workspace_id, "chesscom") != configuration:
        raise ValueError("history_daily_saved_scope_changed")
    spec = manifest.job(configuration)
    job = store.load_job(spec.job_id)
    if job is None or job.spec != spec or job.continuation is None:
        raise ValueError("history_daily_job_mismatch")
    page = parse_page(store.retained_page(spec.job_id, job.continuation), spec.provider)
    if (page.provider, page.account, page.window, page.filters) != (
            spec.provider, spec.account, spec.window, spec.filters):
        raise ValueError("history_daily_page_mismatch")
    candidates = [record for record in page.records
        if (source := store.source(stable_id(spec.workspace_id, spec.provider, record.source_game_id)))
        is not None and source.quarantined]
    if len(candidates) != 1:
        raise ValueError("history_daily_quarantine_count_mismatch")
    record = candidates[0]
    verified = WorkflowNormalizer().normalize(record, spec)
    with admission():
        if review.configuration(manifest.workspace_id, "chesscom") != configuration:
            raise ValueError("history_daily_saved_scope_changed")
        store.recover_daily_history_page(expected=job, configuration=configuration,
            page_document=page.model_dump_json(), record=record, verified=verified)
    return dict(prepared=True, page_records=len(page.records), recovered=1)


def main(compare=False, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "run", "status", "recover-precision", "retry-provider-limit", "recover-daily"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", default="legacy")
    parser.add_argument("--max-units", type=int, default=50)
    options = parser.parse_args(argv)
    if not 1 <= options.max_units <= 10000:
        parser.error("--max-units must be 1..10000")
    if compare and options.operation in {"recover-precision", "retry-provider-limit", "recover-daily"}:
        parser.error("recovery operations apply only to collection")
    writing = options.operation in {"run", "recover-precision", "retry-provider-limit", "recover-daily"}
    variable = "TEMPO_DATABASE_WRITE_URL" if writing else "TEMPO_DATABASE_READ_URL"
    dsn = os.getenv(variable)
    if not dsn:
        parser.error(variable + " is required")
    from .game_review import SystemClock
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    connections = DedicatedGamesConnections(dsn, read_only=not writing)
    store, review, clock = PostgresCanonicalGamesStore(connections), PostgresReviewStore(connections), SystemClock()
    if options.operation == "plan" and not compare and not options.manifest.exists():
        configurations = tuple(c for provider in ("lichess", "chesscom")
            if (c := review.configuration(options.workspace, provider)) is not None)
        if not configurations:
            raise ValueError("historical_saved_configuration_required")
        manifest = new_manifest(options.workspace, configurations)
        save_manifest(options.manifest, manifest)
    else:
        manifest = load_manifest(options.manifest)
    if manifest.workspace_id != options.workspace:
        parser.error("--workspace must match the manifest")
    if options.operation == "plan":
        result = report(store, review, manifest, compare=compare)
        result.update(read_only=True, manifest=str(options.manifest),
            scope=manifest.model_dump(mode="json"),
            feedback_requires="qualified active managed feedback mode" if compare else None)
    elif writing:
        from .game_sync_current import CurrentRepertoireSnapshots
        from app.services.redis_admission_gate import BackgroundAdmissionDeferred
        stopped = Event()
        prior = {s: signal.signal(s, lambda *_: stopped.set()) for s in (signal.SIGINT, signal.SIGTERM)}
        owned = []
        try:
            intake = None
            if compare:
                from .provider_priority import CurrentProviderPriorityIntake
                from .managed_runtime import current_managed_runtime_identity
                from tempo_legacy_database.adapters.games.managed_mode import read_feedback_mode
                intent = read_feedback_mode(connections, manifest.workspace_id)
                if (current_managed_runtime_identity() is None or intent is None
                        or intent.mode != "feedback" or intent.phase != "active"):
                    raise ValueError("historical_qualified_active_feedback_mode_required")
                intake = CurrentProviderPriorityIntake(connections=connections, clock=clock,
                    operation_stopped=stopped.is_set)

            def factory(configuration):
                if compare:
                    return comparison_runner(store=store, snapshots=CurrentRepertoireSnapshots(connections),
                        intake=intake, clock=clock, spec=manifest.job(configuration))
                runtime = create_game_sync(policies=(configuration_policy(configuration),), store=store,
                    snapshots=DeferredSnapshots(), learning=None, clock=clock, source_identity=store,
                    encounter_feedback=True, delivery_enabled=False, preserve_existing_comparison=True)
                owned.append(runtime)
                return runtime.runner

            try:
                if options.operation == "recover-precision":
                    recovery = recover_legacy_precision(manifest, store=store, review=review,
                        admission=historical_admission, stopped=stopped)
                    units = None
                elif options.operation == "retry-provider-limit":
                    recovery = retry_lichess_provider_limit(manifest, store=store, review=review,
                        admission=historical_admission, clock=clock)
                    units = None
                elif options.operation == "recover-daily":
                    recovery = recover_chesscom_daily(manifest, store=store, review=review,
                        admission=historical_admission)
                    units = None
                else:
                    units = run_history(manifest, store=store, review=review, clock=clock,
                        admission=lambda: historical_admission(comparison=compare),
                        stopped=stopped, runner_factory=factory,
                        compare=compare, maximum_units=options.max_units)
                    recovery = None
                deferred = False
            except BackgroundAdmissionDeferred:
                units, deferred, recovery = None, True, None
            result = report(store, review, manifest, compare=compare)
            result.update(bounded_units=units, deferred=deferred, interrupted=stopped.is_set(), recovery=recovery)
        finally:
            for runtime in owned:
                runtime.close()
            for signum, handler in prior.items():
                signal.signal(signum, handler)
    else:
        result = report(store, review, manifest, compare=compare)
        result["read_only"] = True
    operation = (options.operation if options.operation in {"recover-precision","retry-provider-limit", "recover-daily"}
        and not (result.get("recovery") or {}).get("prepared") else "run")
    result["resume"] = f"python scripts/{'check' if compare else 'import'}-game-history.py {operation} --manifest {str(options.manifest)!r} --workspace {manifest.workspace_id!r}"
    print(json.dumps(result, indent=2))
    return 0 if options.operation == "plan" or result["complete"] else 1


def entrypoint(compare=False):
    try:
        return main(compare=compare)
    except (OSError, ValueError) as error:
        reason = str(error)
        if not reason.startswith(("historical_", "history_precision_", "history_provider_limit_", "history_daily_")) or not reason.replace("_", "").isalnum():
            reason = "historical_operation_failed"
        print(json.dumps(dict(complete=False, reason=reason)))
        return 2
    except Exception:
        # No DSNs, SQL parameters or private source material in operator errors.
        print(json.dumps(dict(complete=False, reason="historical_operation_failed")))
        return 2


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] not in {"collect", "compare"}:
        raise SystemExit("usage: python -m app.product_composition.game_history_scripts {collect|compare} {plan|run|status|recover-precision|retry-provider-limit} --manifest PATH")
    phase = sys.argv.pop(1)
    raise SystemExit(entrypoint(compare=phase == "compare"))
