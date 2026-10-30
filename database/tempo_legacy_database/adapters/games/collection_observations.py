"""Bounded Games read projections. No command, provider, or Study table access."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Protocol

import json

from app.contracts.product_v02.learning import GameEffectKey, EncounterReceipt, LearningReceipt


class ReceiptReads(Protocol):
    def encounter_status(self, key: GameEffectKey) -> EncounterReceipt | None: ...
    def effect_status(self, key: GameEffectKey) -> LearningReceipt | None: ...


def provider_name(value: str) -> str:
    if value not in {"lichess", "chesscom", "chess.com"}:
        raise ValueError("unsupported_provider")
    return "chesscom" if value == "chess.com" else value


def aliases(provider: str) -> list[str]:
    return ["chesscom", "chess.com"] if provider_name(provider) == "chesscom" else ["lichess"]


def retained_summaries(findings: list[dict], learner_color: str) -> list[dict]:
    """Agent B's retained-finding projection rule; never replay/classify a game.

    A unique authored own difference needs no physical card or lapse chronology.
    Original kind/diagnostic remain authoritative, including unresolved boundaries.
    """
    grouped = {}
    for finding in sorted(findings, key=lambda item: (item["ply"], item["repertoire_id"])):
        summary = grouped.setdefault(finding["repertoire_id"], {
            "repertoire_id": finding["repertoire_id"], "repertoire_revision": finding["repertoire_revision"],
            "first_departure": None, "first_own_response_miss": None})
        expected = finding["expected_uci"]
        matched = finding["played_uci"] in expected
        own_turn = finding["position_key"].split()[1] == ("w" if learner_color == "white" else "b")
        own_miss = (own_turn and finding["membership"] == "present" and len(expected) == 1
                    and not matched and finding["kind"] not in {"conflict", "coverage_end"})
        if own_miss and summary["first_own_response_miss"] is None:
            summary["first_own_response_miss"] = finding
        if summary["first_departure"] is None and (not matched or finding["kind"] in {
                "conflict", "coverage_end"} or finding["membership"] != "present"
                and finding["kind"] in {"ambiguous", "known_unavailable"}):
            summary["first_departure"] = finding
    return list(grouped.values())


class CollectionObservations:
    """Each query owns a short read-only transaction; failures stay section-local.

    Connections must be the existing DedicatedGamesConnections(read_only=True).
    An injected receipt capability reads the current receiving owner's receipt.
    """

    def __init__(self, connections, *, receipts: Callable[[], ReceiptReads] | None = None,
                 repertoire_readiness: Callable[[str], dict] | None = None):
        if getattr(connections, "read_only", False) is not True:
            raise ValueError("games_reader_profile_required")
        self.connections, self.receipts = connections, receipts
        self.repertoire_readiness = repertoire_readiness

    def _rows(self, statement: str, parameters: tuple = ()) -> list[dict]:
        with self.connections() as database:
            database.execute("SET TRANSACTION READ ONLY")
            cursor = database.execute(statement, parameters)
            names = [column.name for column in cursor.description]
            return [dict(zip(names, tuple(row))) for row in cursor.fetchall()]

    def _section(self, operation):
        try:
            return {"availability": "available", **operation()}
        except Exception as error:
            # Do not expose DSNs, SQL, source payloads or provider accounts in errors.
            return {"availability": "unavailable", "diagnostic": "collection_read_unavailable",
                    "error_type": type(error).__name__}

    def identity(self) -> dict:
        return self._section(lambda: self._rows("""SELECT current_user AS reader_role,
            current_setting('transaction_read_only') AS transaction_read_only,
            (SELECT MAX(version) FROM tempo_schema_migrations) AS schema_version""")[0])

    def providers(self, workspace: str, limit: int, provider: str | None) -> dict:
        def read():
            values = []
            readiness_cache = {}
            for name in (provider,) if provider else ("lichess", "chesscom"):
                spellings = aliases(name)
                configurations = self._rows("""SELECT provider,revision,next_run_at,payload
                    FROM product_feedback_configuration
                    WHERE workspace_id=%s AND provider=ANY(%s) ORDER BY provider LIMIT 3""",
                    (workspace, spellings))
                jobs = self._rows("""SELECT job_id,status,payload->'spec' AS spec,
                    (payload->>'continuation') IS NOT NULL AS continuation_present,
                    payload->'window_complete' AS window_complete,
                    payload->'pages_committed' AS pages_committed,
                    payload->'last_checkpoint_at' AS last_checkpoint_at,
                    payload->'error' AS diagnostic,
                    payload->'next_attempt_at' AS next_attempt_at
                    FROM product_feedback_jobs
                    WHERE payload->'spec'->>'workspace_id'=%s
                    AND payload->'spec'->>'provider'=ANY(%s)
                    AND status IN ('queued','running','retryable') ORDER BY job_id LIMIT %s""",
                    (workspace, spellings, limit + 1))
                latest = self._rows("""SELECT job.job_id,job.status,job.payload->'spec' AS spec,
                    job.payload->'window_complete' AS window_complete,
                    (job.payload->>'continuation') IS NOT NULL AS continuation_present,
                    job.payload->'pages_committed' AS pages_committed,
                    job.payload->'last_checkpoint_at' AS last_checkpoint_at,
                    job.payload->'error' AS diagnostic
                    FROM product_feedback_sync_requests request JOIN product_feedback_jobs job
                    ON job.job_id=request.job_id WHERE request.workspace_id=%s
                    AND request.provider=ANY(%s)
                    ORDER BY request.requested_at DESC,request.operation_id DESC LIMIT 1""",
                    (workspace, spellings))
                coverage = self._rows("""SELECT COUNT(*) AS jobs,
                    COUNT(*) FILTER(WHERE COALESCE((payload->>'window_complete')::boolean,false))
                    AS acquisition_windows_complete,
                    COUNT(*) FILTER(WHERE status='complete') AS terminal_complete_jobs
                    FROM product_feedback_jobs WHERE payload->'spec'->>'workspace_id'=%s
                    AND payload->'spec'->>'provider'=ANY(%s)""", (workspace, spellings))[0]
                config = configurations[0]["payload"] if len(configurations) == 1 else None
                if config is not None:
                    config = {key: config[key] for key in ("workspace_id", "provider", "account", "window",
                        "repertoire_ids", "speeds", "rated", "interval_seconds", "revision") if key in config}
                for job in jobs + latest:
                    job["spec"] = {key: job["spec"][key] for key in ("job_id", "workspace_id", "provider",
                        "account", "window", "repertoire_ids", "filters") if key in job["spec"]}
                selected = config.get("repertoire_ids", []) if config else []
                readiness = []
                for repertoire_id in selected[:8]:
                    if repertoire_id not in readiness_cache:
                        try:
                            observation = (self.repertoire_readiness(repertoire_id) if self.repertoire_readiness else
                                {"availability": "unavailable", "diagnostic": "repertoire_reader_not_supplied"})
                        except Exception:
                            observation = {"availability": "unavailable", "diagnostic": "repertoire_read_unavailable"}
                        readiness_cache[repertoire_id] = deepcopy(observation)
                    readiness.append({**deepcopy(readiness_cache[repertoire_id]), "repertoire_id": repertoire_id})
                fields = ("account", "window.start_at", "window.end_at", "window.precision",
                          "repertoire_ids", "speeds", "rated", "interval_seconds")
                presence = {}
                for field in fields:
                    part = config
                    for key in field.split("."):
                        part = part.get(key) if isinstance(part, dict) else None
                    # Explicit null rated/recurrence are saved policy values.
                    presence[field] = config is not None and (part is not None or field in {"rated", "interval_seconds"} and field in config)
                    if field in {"account", "repertoire_ids", "speeds"}:
                        presence[field] = bool(part)
                values.append({"provider": name, "configured": config is not None,
                    "configuration": config, "configuration_ambiguous": len(configurations) > 1,
                    "selected_repertoire_count": len(selected),
                    "repertoire_readiness": readiness, "repertoires_truncated": len(selected) > 8,
                    "selection_diagnostic": None if selected else "selected_repertoire_ids_absent",
                    "required_inputs": presence, "current_jobs": jobs[:limit],
                    "jobs_truncated": len(jobs) > limit, "latest_job": latest[0] if latest else None,
                    "coverage": coverage})
            return {"items": values}
        return self._section(read)

    def collection(self, workspace: str, limit: int, provider: str | None) -> dict:
        def read():
            spellings = aliases(provider) if provider else ["lichess", "chesscom", "chess.com"]
            counts = self._rows("""WITH sources AS (
                SELECT CASE WHEN payload->'normalized'->>'source_id'='chess.com' THEN 'chesscom'
                    ELSE payload->'normalized'->>'source_id' END provider,
                    payload->'original'->>'source_game_id' game_id,
                    bool_or(COALESCE((payload->>'quarantined')::boolean,false)
                        OR payload->>'diagnostic' IS NOT NULL
                        OR COALESCE(payload->>'comparison_revision','')=''
                        OR EXISTS(SELECT 1 FROM jsonb_array_elements(payload->'findings') f
                            WHERE f->>'membership' IN ('unknown','conflict'))
                        OR jsonb_array_length(COALESCE(payload->'findings','[]'::jsonb))=0) unresolved
                FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'=%s
                AND payload->'normalized'->>'source_id'=ANY(%s) GROUP BY 1,2)
                SELECT COUNT(*) AS unique_games,
                    COUNT(*) FILTER(WHERE unresolved) AS unresolved_games,
                    COUNT(*) FILTER(WHERE NOT unresolved) AS games_with_retained_comparison_evidence
                FROM sources""", (workspace, spellings))[0]
            legacy = self._rows("""SELECT COUNT(*) AS unique_games FROM
                (SELECT CASE WHEN provider='chess.com' THEN 'chesscom' ELSE provider END provider,
                    provider_game_id FROM imported_games WHERE provider=ANY(%s) GROUP BY 1,2) games""",
                (spellings,))[0]
            misses = self._rows("""SELECT COUNT(*) AS unique_own_response_misses FROM (
                SELECT DISTINCT CASE WHEN s.payload->'normalized'->>'source_id'='chess.com'
                    THEN 'chesscom' ELSE s.payload->'normalized'->>'source_id' END provider,
                    s.payload->'original'->>'source_game_id' game_id,
                    f->>'repertoire_id' repertoire_id,f->>'ply' ply,f->'expected_uci' expected,f->>'played_uci' played
                FROM product_feedback_sources s,LATERAL jsonb_array_elements(s.payload->'findings') f
                WHERE s.payload->'normalized'->>'workspace_id'=%s
                    AND s.payload->'normalized'->>'source_id'=ANY(%s)
                    AND NOT COALESCE((s.payload->>'quarantined')::boolean,false)
                    AND f->>'membership'='present' AND jsonb_array_length(f->'expected_uci')=1
                    AND f->'expected_uci'->>0<>f->>'played_uci'
                    AND f->>'kind' NOT IN ('conflict','coverage_end')
                    AND split_part(f->>'position_key',' ',2)=CASE
                        WHEN s.payload->'normalized'->>'learner_color'='white' THEN 'w' ELSE 'b' END
                ) occurrences""", (workspace, spellings))[0]
            recent = self._rows("""SELECT DISTINCT
                CASE WHEN payload->'normalized'->>'source_id'='chess.com' THEN 'chesscom'
                    ELSE payload->'normalized'->>'source_id' END AS provider,
                payload->'original'->>'source_game_id' AS provider_game_id,
                payload->'normalized'->>'game_id' AS canonical_game_id,
                payload->'normalized'->>'learner_color' AS learner_color,
                payload->'original'->>'played_at' AS played_at,
                payload->'original'->>'source_revision' AS source_revision,
                payload->>'comparison_revision' AS comparison_revision,
                payload->>'diagnostic' AS diagnostic
                FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'=%s
                AND payload->'normalized'->>'source_id'=ANY(%s)
                ORDER BY played_at DESC NULLS LAST,provider,provider_game_id LIMIT %s""",
                (workspace, spellings, limit + 1))
            return {**counts, **misses, "legacy_library": legacy, "recent_games": recent[:limit],
                    "truncated": len(recent) > limit,
                    "comparison_completeness": "unresolved" if counts["unresolved_games"] else "not_asserted",
                    "diagnostic": "empty_findings_are_not_comparison_completion_proof"}
        return self._section(read)

    def delivery(self, workspace: str) -> dict:
        def read():
            row = self._rows("""SELECT COUNT(DISTINCT d.effect_key) FILTER(
                    WHERE d.state='pending' AND d.command_kind IN ('lapse','encounter'))
                    AS pending_undelivered_effects,
                COUNT(DISTINCT d.effect_key) FILTER(WHERE d.state='pending' AND d.command_kind='control')
                    AS pending_controls,
                COUNT(*) FILTER(WHERE d.state='acknowledged') AS acknowledged_delivery_records
                FROM product_feedback_deliveries d JOIN product_feedback_sources s USING(source_key)
                WHERE s.payload->'normalized'->>'workspace_id'=%s""", (workspace,))[0]
            return {**row, "observed_execution_mode": "unknown", "globally_disabled": None,
                    "diagnostic": "process_and_delivery_disablement_require_runtime_evidence"}
        return self._section(read)

    def selected_game(self, workspace: str, provider: str, game_id: str) -> dict:
        def read():
            spellings = aliases(provider)
            canonical = self._rows("""SELECT id,provider,provider_game_id,color
                FROM imported_games WHERE provider=ANY(%s) AND provider_game_id=%s
                ORDER BY id LIMIT 3""", (spellings, game_id))
            sources = self._rows("""SELECT source_key,payload->>'comparison_revision' comparison_revision,
                payload->>'diagnostic' diagnostic,payload->'quarantined' quarantined,
                payload->'original'->'source_revision' source_revision,
                payload->'normalized'->'source_revision' canonical_source_revision,
                payload->'normalized'->>'game_id' canonical_game_id,
                payload->'normalized'->>'learner_color' learner_color
                FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'=%s
                AND payload->'normalized'->>'source_id'=ANY(%s)
                AND payload->'original'->>'source_game_id'=%s ORDER BY source_key LIMIT 3""",
                (workspace, spellings, game_id))
            if len(canonical) > 1 or len(sources) > 1:
                return {"diagnostic": "ambiguous_stored_game", "comparison": "unresolved"}
            if not sources:
                return {"provider": provider, "provider_game_id": game_id,
                        "canonical_game_id": canonical[0]["id"] if canonical else None,
                        "learner_color": canonical[0]["color"] if canonical else None,
                        "comparison": "unresolved", "diagnostic": "retained_collection_comparison_absent"}
            source = sources[0]
            findings = self._rows("""SELECT finding FROM product_feedback_sources s,
                LATERAL jsonb_array_elements(s.payload->'findings') finding
                WHERE source_key=%s ORDER BY (finding->>'ply')::int,finding->>'repertoire_id',
                    finding->>'kind',finding->>'diagnostic' LIMIT 1001""", (source["source_key"],))
            deliveries = self._rows("""SELECT d.payload, EXISTS(
                SELECT 1 FROM product_feedback_sources s,
                    LATERAL jsonb_array_elements(s.payload->'effects') original
                WHERE s.source_key=d.source_key AND original ? 'observation'
                    AND original->'key'=COALESCE(d.payload->'control'->'key',
                        d.payload->'encounter'->'key',d.payload->'lapse'->'key')) original_encounter
                FROM product_feedback_deliveries d
                WHERE d.source_key=%s ORDER BY d.operation_id LIMIT 101""", (source["source_key"],))
            receipts = []
            for row in deliveries[:100]:
                from app.games.feedback.interfaces import Delivery
                delivery = Delivery.model_validate_json(json.dumps(row["payload"]))
                receipt = None
                diagnostic = "receipt_not_observed"
                if self.receipts:
                    try:
                        owner = self.receipts()
                        receipt = (owner.encounter_status(delivery.command.key) if delivery.encounter or row["original_encounter"]
                                   else owner.effect_status(delivery.command.key))
                        diagnostic = None if receipt else "not_delivered"
                    except Exception:
                        diagnostic = "owner_receipt_read_unavailable"
                receipts.append({"operation_id": delivery.operation_id, "delivery_state": delivery.state,
                    "receipt": receipt.model_dump(mode="json") if receipt else None,
                    "diagnostic": diagnostic})
            retained = [row["finding"] for row in findings[:1000]]
            return {"provider": provider, "provider_game_id": game_id, **source,
                    "repertoires": retained_summaries(retained, source["learner_color"]),
                    "findings": retained,
                    "findings_truncated": len(findings) > 1000, "receipts": receipts,
                    "deliveries_truncated": len(deliveries) > 100,
                    "comparison": "unresolved" if source["diagnostic"] or source["quarantined"]
                        or not findings or len(findings) > 1000
                        or any(f["membership"] in {"unknown", "conflict"} for f in retained)
                        else "retained_evidence"}
        return self._section(read)

    def report(self, *, workspace: str="legacy", limit: int=20, provider: str | None=None,
               game_id: str | None=None) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("limit_must_be_1_to_100")
        provider = provider_name(provider) if provider else None
        return {"identity": self.identity(), "providers": self.providers(workspace, limit, provider),
                "collection": self.collection(workspace, limit, provider),
                "delivery": self.delivery(workspace),
                "selected_game": self.selected_game(workspace, provider, game_id) if game_id else None}
