"""Pre-ranking bounds for repeated labels; never construct a comparison here."""
from tempo_evidence_math import canonical_json


def comparison_expansion(document, size, alternatives, top_k, *, summaries=False):
    # Keep the original coarse guard, including its allowance for arbitrary
    # metadata/provenance. Account separately for concentration's transformed
    # labels, rather than assuming one copy of input per K is enough.
    coarse = size * (alternatives + 1) * (len(top_k) + 4)
    candidates = document['candidates']
    labels = 0
    identities = 0
    for candidate in candidates:
        identities += len(canonical_json(candidate['card_id']).encode()) + 1
        metadata = candidate['metadata']
        for dimension in ('repertoires', 'frontier_ply', 'routes'):
            if dimension == 'routes':
                values = [canonical_json([route['repertoire_id'], route['generation'], route['line_id']])
                    if all(route.get(key) is not None for key in ('repertoire_id', 'generation', 'line_id')) else None
                    for route in metadata.get('routes', [])]
            else:
                key = 'repertoire_id' if dimension == 'repertoires' else 'frontier_ply'
                values = [membership.get(key) for membership in metadata.get('memberships', [])]
            # No deduplication: every possible selected subset is bounded.
            # Each key appears in both card_mass and shares; 64 bytes allows
            # numeric values, separators and the unknown bucket.
            labels += sum(2 * (len(canonical_json('unknown' if value is None else str(value)).encode()) + 64)
                          for value in (values or [None]))
    # Both production and alternative maps, all K values, and (for corpus)
    # the second copy of top_k in summaries. Fixed per-candidate/metric fields
    # are budgeted independently of input string lengths.
    top = len(top_k) * (2048 + 2 * labels + 2 * identities)
    copies = 2 if summaries else 1
    detailed = (size * (alternatives + 1) * 8
        + alternatives * (4096 + 1024 * len(candidates) + copies * top))
    return max(coarse, detailed)
