# Repertoire learning-priority configuration

Issue #157 adds workspace-owned study preferences for future #158 ranking and #160
Settings controls. It does not change the production queue, admissions, FSRS,
review evidence, opponent probabilities, or policy-conditioned reach.

Italian weight 2 and Ruy weight 1 express relative study emphasis at equal candidate
benefits. They do not imply P(Italian) = 2/3 or guarantee a 2:1 card count.
Downstream consumers may weight final policy study benefits; they must never
multiply individual repertoire reach probabilities by these weights. Uniform
positive scaling preserves relative study ordering. No global ranker is added.

## Read contract

`GET /api/settings/repertoire-learning-priorities` returns:

```json
{
  "schema_version": 1,
  "snapshot_revision": "<64-character SHA-256>",
  "policies": [
    {"policy_id": "<stable repertoire ID>", "learning_priority_weight": 1.0,
     "learning_priority_active": true, "revision": 1}
  ]
}
```

Entries are sorted by ID, include inactive policies, and exclude system and deleted
repertoires. An empty workspace returns an empty list with its own deterministic
fingerprint. The internal snapshot contains frozen records in a tuple; returned
JSON is detached from it. The fingerprint covers schema version, identities,
preferences and per-policy revisions, independently of names, graph publications,
readiness or opponent evidence. Capture and retain this snapshot with a downstream
ranking result; refresh explicitly to select another settings state.

## Update contract

`PUT /api/repertoires/{identifier}/learning-priority` requires all three fields:

```json
{"learning_priority_weight": 2.5, "learning_priority_active": true,
 "expected_revision": 1}
```

Weights are finite nonnegative JSON numbers, including zero and fractions. Strings,
booleans, null, negatives, nonfinite values, unknown fields, missing fields and
invalid flags/revisions return 422. A matching update returns the saved entry;
changed preferences increment its revision once. A matching-revision no-op retains
its revision. Stale writes, including no-op attempts with stale revisions, return
409 with `stale_learning_priority_revision` and `current_revision`. Unknown,
deleted and system identities return 404. Updates fence one policy independently;
editing Italian does not invalidate a pending Ruy update.

Authoritative PostgreSQL writes use the existing foreground command gateway and
`Idempotency-Key`. Retain the same key and payload for uncertain saves. Existing
202 operation polling and failed receipt semantics apply; exact successful replay
returns the original saved entry even if preferences changed later. Read GET again
for current state. A failed stale operation needs a refreshed revision and new key.
SQLite compatibility uses a serialized transaction and stale-write fencing; durable
operation receipts remain the PostgreSQL product contract.

## Identity, lifecycle and storage

`policy_id` is the existing `repertoires.id` used by selected-policy contracts.
Weights are not inferred from imported line counts, import order, games or popularity.
New and migrated user repertoires start at weight 1, active, revision 1. Same-source,
same-color re-import resolves the existing ID; rename and ordinary policy/settings
updates preserve preference state. White and Black imports receive their existing
separate identities. A different source is a new identity under existing import
resolution, rather than an implicit transfer of preferences.

Inactive policies retain their configured weight; downstream consumers must treat
their direct contribution as zero. Zero weight also suppresses only that policy's
direct contribution. A truly shared card remains eligible for a nonzero active
policy's contribution. `learning_priority_active` is study-allocation state and is
independent of `is_main`, which only selects the primary repertoire. Neither flag
nor zero weight changes today's production admissions. Reactivation preserves the
stored weight. Deletion removes configuration; re-import after deletion creates a
new ID with neutral defaults. System/retained-card repertoires are never study policies.

Three constrained columns on `repertoires` provide durable preferences and the
independent revision. Updates lock that row briefly and write only those columns.
They do not invoke queue/profile refresh handlers, rebuild graphs, invalidate caches,
run provider/engine work or rewrite history. There is no additional settings store,
scheduler, quota or actual-game policy mixture.
