"""Pure eligibility/preference. Ordinary input order is already authoritative."""
from .models import AdmissionProposal, Candidate, Eligibility

PAGE_SIZE = 16
MAX_PREPARATION_PAGES = 4


def evaluate_eligibility(candidate: Candidate) -> Eligibility:
    path = "review" if candidate.introduced else "introduction"
    for blocked, reason in ((candidate.deleted, "card_deleted"), (candidate.retired, "card_retired"),
                            (not candidate.content_valid, "content_invalid"),
                            (not candidate.current_revision, "stale_revision"),
                            (candidate.buried, "buried_today")):
        if blocked:
            return Eligibility(False, reason, path)
    if not candidate.introduced and not candidate.outstanding_miss:
        if not candidate.progression_allowed:
            return Eligibility(False, "curriculum_blocked", path)
        if candidate.remaining_allowance <= 0:
            return Eligibility(False, "allowance_exhausted", path)
    return Eligibility(True, None, path)


def order_candidates(candidates: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
    """Stable priority overlay after a protected prefix; never recompute ranks."""
    protected = [candidate for candidate in candidates if candidate.protected]
    selected = {candidate.card_id for candidate in protected}
    movable = []
    for candidate in candidates:
        if candidate.card_id not in selected:
            movable.append(candidate)
            selected.add(candidate.card_id)
    misses = sorted((candidate for candidate in movable if candidate.outstanding_miss),
                    key=lambda candidate: (candidate.card_id, candidate.route_id))
    return tuple(protected + misses + [candidate for candidate in movable if not candidate.outstanding_miss])


def propose_admission(candidate: Candidate) -> AdmissionProposal | None:
    eligibility = evaluate_eligibility(candidate)
    if not eligibility.eligible or candidate.protected:
        return None
    return AdmissionProposal(candidate.card_id, candidate.card_revision, candidate.publication_id,
                             candidate.publication_generation, candidate.route_id, eligibility.path,
                             "real_game_miss" if candidate.outstanding_miss else "ordinary")
