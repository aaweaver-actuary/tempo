# Prefix difficulty diagnostics

Repertoire → More → Prefix difficulty opens a read-only panel. Select a current multi-decision prefix to inspect each learner decision. Whole opening cards retain scheduling ownership; diagnostics never change boundaries, FSRS, cards, queues, or repertoire structure.

## Evidence contract

The source is PR #69's persisted `opening_evidence_observations.observation_json`, already produced by `reduce_observations`. Diagnostics do not replay journals, reconstruct attempts, alter event interpretation, repair checkpoints, or decompose aggregate grades. Active, partial, and complete durable attempts all contribute their reached observations. Setup/opponent moves and unreached decisions receive no credit. An earlier clean response survives later failure in the same attempt.

The view exposes reached observations; all first responses/failures; unassisted first responses/failures; clean successes; assistance before response and its categories; manual failures; corrections/reveals; distinct clean study days; distinct unassisted response days; and up to 20 recent reached observations per decision. A response is unassisted only when an actual first-response UCI exists, pre-response assistance is empty, and grading disposition is neither illegal nor unverified. Failed first responses can establish coverage but cannot create clean recall. Clean success uses the reducer's `clean` flag verbatim. Later assistance preserves the reducer's already established first-response fact; pre-response hints/reveals/guidance and subsequent corrections never create clean success.

Day counts use each observation's original `study_day`, frozen by PR #69 in the attempt's study timezone. Same-day retries count once. Unknown coverage means no evaluable unassisted responses. Weak coverage has fewer than three distinct unassisted response days; strong coverage has at least three, regardless of success. These are coverage labels, not difficulty, statistical confidence, easy/hard ratings, or depth recommendations. Distinct clean-success days are shown separately.

## Scope and read bounds

GET `/api/repertoires/{identifier}/prefix-diagnostics` lists eight current published multi-decision prefixes, with a ninth lookahead and keyset `after_card_id`. Subsequent pages supply `graph_generation`; changed or unfinished publications require refresh. Membership, archive status, integrity blocks, saved content, and graph color constrain the available presentation. Missing/ambiguous contexts are unavailable rather than inferred.

GET `/api/repertoires/{identifier}/prefix-diagnostics/{card_id}` requires the listed `manifest_id` and `graph_generation`. Evidence is restricted by exact presentation snapshot, repertoire, captured learner color, card and revision; the derived manifest must also match. Observation indices keep separate occurrences of the same decision identity distinct. Changing revision does not inherit earlier recall credit. Historical shadow evidence remains stored.

Counts cover the latest 100 scoped attempts, ordered by original `started_at` descending with attempt-ID tie-breaks. A 101st header detects excluded older evidence; this is not a lifetime summary. Selected attempts have at most 20 decisions, so observation reads return at most 2,000 rows. Recent outcomes use original observation instants and stable attempt/index tie-breakers within that window. Empty attempts consume a window slot but contribute no observations. The index on presentation/repertoire/color/start time bounds history lookup; a partial graph index supports prefix pagination. There are no history aggregates or derived-data writes.

Both endpoints are secondary even without a client work-class header. They use read-only repeatable-read connections to the configured PostgreSQL reader (the API does not need writer credentials) under existing local/Redis admission, with the existing default 250ms transaction and 25ms lock limits. Database connections close before manifest traversal, JSON decoding, aggregation, or rendering. The UI fetches only after navigation to the panel and explicit prefix selection, uses background GETs, and cancels/ignores stale requests. Capture, queue preparation, board input and review handling do not call this service.

## Limits

Only PostgreSQL is supported; SQLite and the practice demo cannot provide authoritative decision evidence. Service/context errors remain actionable errors, never sample data or false success. Unsynced or rejected evidence may be absent. Older evidence outside the window is excluded and explicitly disclosed. No response latency is calculated: the journal lacks a defensible decision-ready timestamp. No automatic recommendation or optimal-prefix-depth classifier is included.
