# Position-owned opening Study contract

`contracts.py` contains detached immutable values and transaction ports only. It
performs no issuance, grade, graph lookup, persistence, or scheduling. Existing
Study remains authoritative for these operations and the legacy card scheduler
and its first-clean reinforcement remain unchanged. The pure values do not
activate a cohort or attest that an operation occurred.

`PositionKey(repertoire_id, position, variant)` is the stable schedule identity.
`position` must already be the canonical legal four-field FEN from
`tempo_evidence_math.opening_position_key`, including legal en passant, without
clocks. The definition separately fences the selected UCI and policy version.
A response/policy change holds an old attempt's positive effect until the owner
revalidates its exact original definition; it never moves history to another key.
Existing card ID plus occurrence ply, evidence-math decision ID, and Games event
IDs remain original provenance, not aliases or rewritten keys. A mapping requires
source owner proof of the exact repertoire, occurrence, board and answer; a FEN
or physical card alone cannot establish it.

`SourceCertificate` and `RouteOption` carry claims from the Repertoire owner;
constructing them or setting their booleans does not certify their provenance,
completeness, legality or currentness. The existing snapshot/index owner must
establish those facts and the Study issuer must fence them in its transaction.
Authored-route completeness is separate from physical-card binding ambiguity.
The original queue/card/attempt identity, frozen plan, definition, source proof,
owner epoch and a due witness for every physically shown learner position must
be persisted together. A proposed plan is not an issued receipt. A matching
retry returns the original issuance; changed bytes under the same identity hold.

The writer uses the captured due witness as necessary, never sufficient, positive
entitlement. Under the existing borrowed transaction it locks position states in
stable key order, rechecks definition, source and owner epoch, event identity,
current revision and the accepted positive ledger, and commits schedule, receipt
and original attempt/queue settlement atomically. Stale Good is observation;
never backdate or reorder an accepted FSRS transition. A newly due state after
issuance cannot turn a nondue witness into Good. A source or epoch mismatch holds;
no silent fallback to legacy scheduling for an opted-in position. Study reads its
configured local timezone and captures the Study day once at issuance. To bound
late arrivals, a Good is eligible only when acceptance's current local Study day
is the same as the issued day; after midnight preserve the response as observation,
including on replay, and never credit the prior day retroactively. The writer
checks at most one Good per position and local day across all attempts, policies
and epochs. Negative evidence is accepted on the current owner day at actual
processing time, including after midnight, without backdating. Timezone changes
must be fenced by the owner; a mismatched or unavailable day context holds.

| Evidence and current ledger | Owner outcome |
| --- | --- |
| Saved completed attempt, unaided correct first response, due at issuance, unchanged definition/epoch and revision, no Good that day | Good once |
| Correct but nondue, assisted, stale state, previous Good, or accepted after issued day | Observe only |
| Correct but no completed saved attempt | Pending; no Good |
| New durable authoritative wrong first response or explicit manual failure, due or nondue, even after Good or abandoned completion | Again once at acceptance |
| Guided retry or duplicate same first-response event | No new transition; replay original receipt |
| Alternate or unverified response, missing durable evidence, changed definition or epoch | Hold, no fabricated failure |
| Two tabs issued due at same revision | First qualifying Good advances state; second Good observes after ledger/revision recheck; a distinct later wrong still gets Again |

A partially correct line cannot certify prefix skipping. That requires a saved,
whole clean presentation with matching ordered authored prefix, root, day, source
and epoch. A correct response in a subsequently failed line may still be graded
individually when its saved complete attempt meets the positive conditions.

Example detached values (the owner must still validate and persist them):

```python
key = PositionKey("rep-1", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -")
definition = PositionDefinition(key, "e2e4", "policy-1")
# DueAtIssuance(definition, True, local_day, epoch, revision, issued_at)
# Each FirstResponse is tied to the original attempt and one durable event ID.
```

The interfaces deliberately have no Repertoire implementation, DB connection,
provider or scheduler import. Concrete issuance and grade receipts are supplied
by the actual Study owner; ports borrow its transaction and cannot commit it.
