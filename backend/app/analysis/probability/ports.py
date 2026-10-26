"""Explorer's bounded request and capabilities; shared evidence stays T01-owned."""
from typing import Literal, Protocol

from pydantic import Field, model_validator
from tempo_evidence_math import MoveCount, PositionMoveUniverse

from app.contracts.product_v02 import Identifier, UciMove, UtcTimestamp, Value
from app.contracts.product_v02.analysis import PopulationSelection, ProbabilityEvidence


class ExplorerRequest(Value):
    position: PositionMoveUniverse
    starting_fen: str = Field(min_length=1, max_length=120)
    history_uci: tuple[UciMove, ...] = Field(max_length=512)
    variant: Identifier
    population: PopulationSelection | None
    since: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    until: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    move_limit: int = Field(ge=1, le=256)
    max_requests: Literal[1] = 1
    timeout_seconds: float = Field(default=20.0, gt=0, le=20)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=5)
    max_response_bytes: int = Field(default=1024 * 1024, ge=1, le=1024 * 1024)
    cache_max_age_seconds: int = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.since > self.until or self.connect_timeout_seconds > self.timeout_seconds:
            raise ValueError("invalid request bounds")
        return self


DiagnosticCode = Literal[
    "configuration_required", "unsupported_population", "unsupported_variant",
    "invalid_request", "credential_missing", "credential_rejected", "rate_limited",
    "provider_unavailable", "provider_request_rejected", "provider_timeout",
    "invalid_response", "response_too_large", "result_limit_exceeded", "cancelled",
    "cache_unavailable", "invalid_cache_entry",
]


class ExplorerDiagnostic(Value):
    code: DiagnosticCode
    retryable: bool = False
    retry_at: UtcTimestamp | None = None
    auth_failed: bool = False


class ExplorerResult(Value):
    """Local acquisition envelope, never a second probability DTO.

    Missing configuration has no evidence: T01 correctly requires an explicit
    population for population evidence. All other failures retain that context
    in shared unavailable ProbabilityEvidence.
    """
    request_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: ExplorerRequest
    source: Identifier
    adapter_version: Literal["explorer-v1"] = "explorer-v1"
    evidence: ProbabilityEvidence | None
    diagnostic: ExplorerDiagnostic | None
    # T01 CountEvidence needs a known denominator. Retain known per-move counts
    # separately when that denominator is missing, using the same public type.
    reported_counts: tuple[MoveCount, ...] = ()
    from_cache: bool = False

    @model_validator(mode="after")
    def bind_result(self):
        evidence = self.evidence
        if self.diagnostic is not None:
            if self.reported_counts or self.from_cache or (evidence is not None and evidence.distribution is not None):
                raise ValueError("failure cannot claim successful sample evidence")
        elif evidence is None or evidence.distribution is None:
            raise ValueError("success requires shared probability evidence")
        if evidence is not None and (evidence.position != self.request.position
                or evidence.population != self.request.population
                or evidence.provider != "lichess-explorer" or evidence.model is not None):
            raise ValueError("evidence context does not match request")
        moves = [item.move_uci for item in self.reported_counts]
        if len(set(moves)) != len(moves) or not set(moves).issubset(self.request.position.legal_moves):
            raise ValueError("reported counts must refer to unique legal moves")
        if evidence is not None and evidence.counts is not None and (
                sorted(evidence.counts.moves, key=lambda item: item.move_uci)
                != sorted(self.reported_counts, key=lambda item: item.move_uci)):
            raise ValueError("reported counts disagree with shared sample evidence")
        return self


class ExplorerCache(Protocol):
    """Each operation releases its own resource/transaction before returning.

    No borrowed connection, lease or context manager crosses this seam. Only
    successful immutable results are offered for storage. Composition owns
    persistence, eviction and capacity; keys are opaque and credential-free.
    """
    async def get(self, key: str) -> ExplorerResult | None: ...
    async def put(self, key: str, value: ExplorerResult) -> None: ...


class Cancellation(Protocol):
    def is_set(self) -> bool: ...
    async def wait(self) -> object: ...


class ExplorerProbabilityProvider(Protocol):
    async def fetch(self, request: ExplorerRequest, *, credential: str | None,
                    cancellation: Cancellation | None = None) -> ExplorerResult: ...
