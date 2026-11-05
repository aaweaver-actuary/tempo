"""Prepare a deterministic issuance outside the Study writer transaction.

The capability contains claims, not a receipt. The database owner checks it
against the sealed source, admission, and every current schedule witness.
"""

from dataclasses import dataclass, replace

from tempo_evidence_math.position_learning import Hold
from .issued_plan_contracts import SelectionRequest, select_and_freeze
from typing import Any

_SEAL = object()


@dataclass(frozen=True, init=False)
class PreparedPositionIssuance:
    source: Any
    request: SelectionRequest
    plan: object
    _seal: object

    def __init__(self, seal, source, request, plan):
        if seal is not _SEAL:
            raise ValueError("position_issuance_preparation_required")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "_seal", seal)


def prepare_position_issuance(source: Any, request: SelectionRequest):
    """Select from the owner's full prepared routes; no database or graph work here.

    Prefix claims remain proposals; the writer re-reads their durable evidence.
    """
    if not isinstance(request, SelectionRequest) or not all(
        hasattr(source, name) for name in ("routes", "authored_token")
    ):
        return Hold("position_issuance_preparation_required")
    if request.routes != source.routes:
        return Hold("position_routes_not_owner_prepared")
    if not request.dues or len({d.issued_at for d in request.dues}) != 1:
        return Hold("position_due_instant_mismatch")
    request = replace(request, authored_source_token=source.authored_token.as_dict())
    plan = select_and_freeze(request)
    if isinstance(plan, Hold):
        return plan
    request = replace(request, dues=plan.due_positions)
    return PreparedPositionIssuance(_SEAL, source, request, plan)


def is_prepared_position_issuance(value):
    return isinstance(value, PreparedPositionIssuance) and value._seal is _SEAL
