"""Prepare authored Study positions from one bounded storage snapshot."""
from __future__ import annotations

import json

from tempo_evidence_math.position_learning import Hold, SourceCertificate, PreparedPositionSourceView, PreparedPositionSourceWithFactsView
from tempo_storage.authored_repertoires.product_snapshot import read_repertoire
from tempo_storage.authored_repertoires.position_study_source import (
    AuthoredSourceToken,
    read_owner_header, authored_digest, prepared_source,
)


def _prepare_position_source(database, repertoire_id, *, retain_facts):
    from app.repertoire.snapshot import PreparedPublishedRepertoire
    from app.repertoire.interfaces.position_schedule_occurrences import routes_from_snapshot
    try:
        facts = read_repertoire(database, repertoire_id)
        if facts["repertoire"]["id"] != repertoire_id:
            raise ValueError("position_source_scope_mismatch")
        published = PreparedPublishedRepertoire(facts)
        snapshot = published.snapshot
        header, integrity = read_owner_header(database, repertoire_id, lock=False)
        if any(facts["repertoire"][k] != header[k] for k in header):
            raise ValueError("position_source_changed_during_preparation")
        membership = snapshot.membership
        provenance = SourceCertificate(membership.snapshot_id, membership.source_revision,
                                       membership.digest, True, True)
        routes = routes_from_snapshot(snapshot, source_certificate=provenance)
        if isinstance(routes, Hold):
            return routes
        token = AuthoredSourceToken(membership.snapshot_id, membership.source_revision, membership.digest,
            repertoire_id, authored_digest(header, facts["lines"]),
            json.dumps(header, sort_keys=True, separators=(",", ":")),
            json.dumps(integrity, sort_keys=True, separators=(",", ":")))
        return prepared_source(snapshot, routes, token,
            publication_facts=published.publication_facts() if retain_facts else None)
    except (ValueError, TypeError, KeyError) as exc:
        return Hold("position_source_preparation_unavailable", str(exc))



def prepare_position_source_with_facts(database, repertoire_id: str) -> PreparedPositionSourceWithFactsView | Hold:
    """Prepare source and preserve the same complete read, outside the writer."""
    return _prepare_position_source(database, repertoire_id, retain_facts=True)



def prepare_position_source(database, repertoire_id: str) -> PreparedPositionSourceView | Hold:
    """Read and legally project a complete publication outside the writer."""
    return _prepare_position_source(database, repertoire_id, retain_facts=False)
