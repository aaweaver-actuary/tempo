# Study application

Study owns queue selection, issuance, review scheduling and receipt identity. The
retained policies live in `policy`, `frozen_order`, `openings` and `review`;
composition supplies storage and command capabilities rather than giving Games a
scheduler or writer.

The three GET routes `/api/queue/today`, `/api/queue/prepared` and
`/api/queue/window` delegate through `product_composition.study.queue_next_exercise`
to `next_exercise.next_exercise`. It reads a detached snapshot, issues or replays
the head, rechecks the current day and head, and finalizes the flat queue. Position
issuance replays the original plan through `position_learning.issuance` and builds
its manifest through `position_checkpoint`; a missing or invalid plan remains
held. The prepared route adds exercise snapshots and `prepared_at`.

Ready and empty outcomes return the flat queue (`local_date`, `cards`, `count`,
`diagnostics`, `projection`); a hold may retain that queue, while an unavailable
outcome preserves its reason. A pending dispatcher response retains the original
operation identity and transport headers. Explicit foreground admission denial
uses `foreground_wait`; arbitrary failures are not reclassified as that denial.
`queue.prepare_next` and optional feedback preparation are retired. Existing
attempts, review receipts and history keep their original identities.

See [storage](../../../database/tempo_storage/README.md) and the
[Study source map](../../../docs/architecture/diagrams/study-games.md). Source
behavior does not establish native or assembled qualification.
