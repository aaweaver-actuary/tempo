"""Bounded source-edit decisions; no graph replay, scheduling or history writes.

Facts must be prepared and fenced by the Repertoire owner. Detached certificates
are claims, not authority. This module never authorizes a grade or reuses prefix
credit: consumers must reprepare changed presentations and revalidate saved
attempts against their original immutable proof through the existing owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tempo_evidence_math.position_learning import (
    PositionDefinition,
    PositionKey,
    RouteOption,
    SourceCertificate,
)


@dataclass(frozen=True)
class ScopedPositionSource:
    """Complete owner facts for ONE exact position, including all route aliases.

    Empty routes prove removal only under a complete, current certificate.
    ``authored_digest`` is the authored-content token; ``certificate.source_digest``
    remains the original full publication digest, including physical mappings.
    They are intentionally never compared to one another.
    """

    key: PositionKey
    certificate: SourceCertificate
    authored_digest: str
    routes: tuple[RouteOption, ...]


@dataclass(frozen=True)
class SourceChangeDecision:
    position: PositionKey
    disposition: Literal[
        "preserve_schedule",
        "hold_definition",
        "retire_new_issuance",
        "independent",
        "new",
    ]
    reason: str
    allow_new_issuance: bool
    reuse_schedule: bool
    requires_reauthorization: bool
    reprepare_presentation: bool
    added_route_ids: tuple[str, ...]
    retired_route_ids: tuple[str, ...]

    def as_record(self) -> dict:
        """Primitive read model for consumers without backend-private imports.

        No disposition deletes stored memory/history or rewrites an old attempt.
        ``reuse_schedule`` permits the SAME complete FSRS state, never a copy to
        another position. Reauthorization must be explicit and owner-validated.
        """
        return {
            "position": {
                "repertoire_id": self.position.repertoire_id,
                "position": self.position.position,
                "variant": self.position.variant,
            },
            "disposition": self.disposition,
            "reason": self.reason,
            "allow_new_issuance": self.allow_new_issuance,
            "reuse_schedule": self.reuse_schedule,
            "requires_reauthorization": self.requires_reauthorization,
            "reprepare_presentation": self.reprepare_presentation,
            "added_route_ids": list(self.added_route_ids),
            "retired_route_ids": list(self.retired_route_ids),
            "retain_stored_schedule": True,
            "retain_original_attempt_proof": True,
            "retain_accepted_grade_history": True,
            "prior_attempt_rule": (
                "hold_positive_until_explicit_reauthorization"
                if self.requires_reauthorization
                else "revalidate_original_attempt_proof"
            ),
        }


def _facts(
    source: ScopedPositionSource,
) -> tuple[tuple[PositionDefinition, ...], tuple[str, ...]] | str:
    cert = source.certificate
    if not cert.complete or not cert.current_integrity or not source.authored_digest:
        return "source_integrity_unavailable"
    if len(source.routes) > 64 or sum(len(r.moves_uci) for r in source.routes) > 4096:
        return "scoped_source_exceeds_bound"
    definitions = set()
    route_ids = set()
    for route in source.routes:
        if not route.legal_verified or route.certificate != cert:
            return "route_source_unproven"
        if route.route_id in route_ids:
            return "ambiguous_route_identity"
        route_ids.add(route.route_id)
        occurrences = tuple(
            d for d in route.decisions if d.definition.key == source.key
        )
        if not occurrences or any(
            d.definition.key.repertoire_id != source.key.repertoire_id
            for d in route.decisions
        ):
            return "route_scope_mismatch"
        definitions.update(d.definition for d in occurrences)
    if len(definitions) > 1:
        return "conflicting_position_definitions"
    return tuple(definitions), tuple(sorted(route_ids))


def compare_source_change(
    before: ScopedPositionSource | None,
    after: ScopedPositionSource,
) -> SourceChangeDecision:
    """Compare scoped source facts without interpreting global digest changes.

    A missing prior scope is new work. A different key is independent work; the
    caller compares the old key separately against its own current scoped facts.
    A complete empty current scope retires fresh issuance, retaining old attempts.
    A response/policy change holds existing memory pending explicit authorization.
    Changed routes/source require fresh preparation, never a reset or a new key.
    """
    current = _facts(after)
    prior = _facts(before) if before is not None and before.key == after.key else None

    def result(
        disposition,
        reason,
        *,
        issue=False,
        reuse=False,
        reauthorize=False,
        reprepare=True,
        added=(),
        retired=(),
    ):
        return SourceChangeDecision(
            after.key,
            disposition,
            reason,
            issue,
            reuse,
            reauthorize,
            reprepare,
            added,
            retired,
        )

    if isinstance(current, str) or isinstance(prior, str):
        return result(
            "hold_definition",
            current if isinstance(current, str) else prior,
            reauthorize=True,
        )
    definitions, routes = current
    if before is not None and before.key != after.key:
        return result(
            "independent",
            "different_exact_position_or_repertoire",
            issue=bool(definitions),
            added=routes,
        )
    prior_definitions, prior_routes = prior if prior is not None else ((), ())
    added = tuple(sorted(set(routes) - set(prior_routes)))
    retired = tuple(sorted(set(prior_routes) - set(routes)))
    if not definitions:
        return result(
            "retire_new_issuance", "no_current_authored_route", retired=retired
        )
    if not prior_definitions:
        return result("new", "new_scoped_definition", issue=True, added=added)
    old, new = prior_definitions[0], definitions[0]
    if old.expected_uci != new.expected_uci or old.policy_version != new.policy_version:
        return result(
            "hold_definition",
            "selected_response_changed"
            if old.expected_uci != new.expected_uci
            else "content_policy_changed",
            reauthorize=True,
            added=added,
            retired=retired,
        )
    return result(
        "preserve_schedule",
        "same_position_and_approved_response",
        issue=True,
        reuse=True,
        reprepare=before != after,
        added=added,
        retired=retired,
    )


def compare_persisted_definition(
    previous: PositionDefinition,
    current: ScopedPositionSource | None,
) -> SourceChangeDecision:
    """Compare a locked Study-state definition with current owner source facts.

    The caller reads ``previous`` from authoritative persisted state, never a
    client. No old route snapshot/certificate is required or reconstructed. New
    facts still require the source owner's preparation and transaction fence.
    ``None`` means unavailable authority, not proved removal; removal requires
    a complete certified empty scope. Unknown prior routes cannot establish
    added/retired aliases, prefix credit or permission to reuse a prepared plan.
    """
    key = current.key if current is not None else previous.key

    def result(disposition, reason, *, issue=False, reuse=False, reauthorize=False):
        return SourceChangeDecision(
            key, disposition, reason, issue, reuse, reauthorize, True, (), ()
        )

    if current is None:
        return result("hold_definition", "current_source_unavailable", reauthorize=True)
    facts = _facts(current)
    if isinstance(facts, str):
        return result("hold_definition", facts, reauthorize=True)
    definitions, _ = facts
    if previous.key != current.key:
        return result(
            "independent",
            "different_exact_position_or_repertoire",
            issue=bool(definitions),
        )
    if not definitions:
        return result("retire_new_issuance", "no_current_authored_route")
    selected = definitions[0]
    if previous.expected_uci != selected.expected_uci:
        return result("hold_definition", "selected_response_changed", reauthorize=True)
    if previous.policy_version != selected.policy_version:
        return result("hold_definition", "content_policy_changed", reauthorize=True)
    return result(
        "preserve_schedule",
        "same_position_and_approved_response",
        issue=True,
        reuse=True,
    )
