"""Bounded orchestration; A supplies trusted facts and the sole admission writer."""
from dataclasses import dataclass, replace
from typing import Protocol

from .models import AdmissionPort, AdmissionResult, Candidate, WorkItem
from .policy import MAX_PREPARATION_PAGES, PAGE_SIZE, evaluate_eligibility, order_candidates, propose_admission
from tempo_remediation_database.adapters.repository import PostgresRepository


class CandidateFactsPort(Protocol):
    def candidates(self, items: tuple[WorkItem, ...]) -> tuple[Candidate, ...]: ...


@dataclass(frozen=True)
class Preparation:
    candidates: tuple[Candidate, ...]
    cursor: str
    has_more: bool
    examined_pages: int


def prepare_admissions(repository: PostgresRepository, facts: CandidateFactsPort,
                       cursor: str = "", *, page_size: int = PAGE_SIZE,
                       max_pages: int = MAX_PREPARATION_PAGES) -> Preparation:
    if not 1 <= page_size <= PAGE_SIZE or not 1 <= max_pages <= MAX_PREPARATION_PAGES:
        raise ValueError("preparation exceeds the module bounds")
    candidates = []
    has_more = False
    for page_index in range(max_pages):
        page = (repository.pending_page(cursor) if page_size == PAGE_SIZE else
                repository.pending_page(cursor, limit=page_size))
        cursor, has_more = page.cursor, page.has_more
        outstanding = {item.card_id for item in page.items}
        supplied = facts.candidates(page.items) if page.items else ()
        if len(supplied) > page_size:
            raise ValueError("candidate facts must be bounded to one route per requested card")
        if any(candidate.card_id not in outstanding for candidate in supplied):
            raise ValueError("candidate facts escaped the requested page")
        candidates.extend(replace(candidate, outstanding_miss=True) for candidate in supplied
                          if repository.has_current_miss(candidate))
        if not has_more:
            break
    return Preparation(order_candidates(tuple(candidates)), cursor, has_more, page_index + 1)


def apply_admission(repository: PostgresRepository, candidate: Candidate,
                    writer: AdmissionPort) -> AdmissionResult:
    """Exactly one application in A's transaction, AFTER A acquires its locks.

    writer is A's prelocked admission capability. It must not acquire any new
    encompassing card/queue locks after the module lock (see README lock order).
    """
    repository.lock()
    if not repository.has_current_miss(candidate):
        result = AdmissionResult("rejected", "obligation_not_current")
        repository.record_admission(candidate.card_id, result)
        return result
    current = replace(candidate, outstanding_miss=True)
    proposal = propose_admission(current)
    if proposal is None:
        result = AdmissionResult("deferred", evaluate_eligibility(current).reason or "issued_head_preserved")
    else:
        result = writer.apply(proposal)
    repository.record_admission(candidate.card_id, result)
    return result
