"""Read-only diagnostics over the v1 shadow observations; no scheduling contract."""
from typing import Literal
from pydantic import Field
from .opening_evidence_contracts import EvidenceModel, OpeningDecisionManifest, OpeningManifestDecision, AssistanceKind


class DiagnosticPrefix(EvidenceModel):
    card_id: str
    presentation_san: str | None
    manifest: OpeningDecisionManifest | None
    unavailable_reason: str | None


class PrefixDiagnosticsList(EvidenceModel):
    version: Literal[1] = 1
    repertoire_id: str
    graph_generation: int
    prefixes: list[DiagnosticPrefix] = Field(max_length=8)
    next_card_id: str | None


class DiagnosticObservation(EvidenceModel):
    attempt_id: str
    state: Literal['active', 'partial', 'complete']
    decision_index: int
    decision_id: str
    expected_uci: str
    first_response_uci: str | None
    first_response_correct: bool | None
    assistance_before_response: list[AssistanceKind]
    assistance: list[AssistanceKind]
    manual_failure: bool
    revealed: bool
    corrected: bool
    observed_at: str
    response_at: str | None
    failure_at: str | None
    disposition: Literal['expected', 'alternate', 'wrong', 'illegal', 'unverified'] | None
    clean: bool
    study_day: str


class DecisionDiagnostics(OpeningManifestDecision):
    coverage: Literal['unknown', 'weak', 'strong']
    reached_observations: int
    first_responses: int
    first_response_failures: int
    unassisted_first_responses: int
    unassisted_first_response_failures: int
    clean_successes: int
    assistance_before_response: int
    assistance_categories: dict[AssistanceKind, int]
    manual_failures: int
    corrections: int
    reveals: int
    distinct_clean_days: int
    distinct_unassisted_response_days: int
    recent_outcomes: list[DiagnosticObservation] = Field(max_length=20)


class DiagnosticWindow(EvidenceModel):
    attempt_limit: Literal[100] = 100
    attempt_count: int = Field(ge=0, le=100)
    older_attempts_excluded: bool
    newest_started_at: str | None
    oldest_started_at: str | None


class PrefixDiagnosticsDetail(EvidenceModel):
    version: Literal[1] = 1
    read_only: Literal[True] = True
    graph_generation: int
    manifest: OpeningDecisionManifest
    window: DiagnosticWindow
    decisions: list[DecisionDiagnostics] = Field(min_length=1, max_length=20)
