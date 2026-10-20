# Browser persistence

Current source definitions; existing browser data/version state was not inspected. Owner: the Study/browser client. These are IndexedDB object stores and serialized JavaScript records, not PostgreSQL tables. Key paths are enforced by IndexedDB; field shapes are TypeScript/runtime validation contracts. IndexedDB declares **no foreign keys**.

| File | Data / authority |
| --- | --- |
| [tempo-db.ts](tempo-db.ts) | `tempo`, version 1; portable snapshot records in ten stores below. |
| [offline-training-storage.ts](offline-training-storage.ts) | `tempo-offline-training` or the workspace-specific name from `study-connection`; version 2; prepared queue and incremental opening journal stores. |
| [study-intent-storage.ts](study-intent-storage.ts) | Browser intent package adapter and legacy localStorage cutover/checkpoints; uses the existing [intent package](../browser-intents/README.md). |
| [sqlite-migration.ts](sqlite-migration.ts) | Existing HTTP snapshot-to-IndexedDB import; `tempo-sqlite-migration` localStorage completion marker. |
| [encrypted-backup.ts](encrypted-backup.ts) | Existing encrypted export/import of portable records. Envelope fields: `version: 1`, `algorithm: PBKDF2-AES-GCM`, `iterations: number`, `salt/iv/ciphertext: string`. |

## Portable records

All ten actual `tempo` stores have the same record shape: `key: string` (primary key path), `value: unknown` (stored source row/payload), and optional `updatedAt: string`. Store names identify stored data: `repertoires`, `lines`, `cards`, `reviews`, `queues`, `annotations`, `games`, `settings`, `syncMetadata`, `tombstones`. The importer derives record identities from each table's existing row identity; source PostgreSQL constraints do not become IndexedDB constraints.

```mermaid
erDiagram
  repertoires { string key PK }
  lines { string key PK }
  cards { string key PK }
  reviews { string key PK }
  queues { string key PK }
  annotations { string key PK }
  games { string key PK }
  settings { string key PK }
  syncMetadata { string key PK }
  tombstones { string key PK }
```

## Prepared training and opening journal

```mermaid
erDiagram
  training { string external_record_key PK }
  opening_attempts { string attempt_id PK }
  opening_events {
    string attempt_id PK
    number sequence PK
  }
  opening_attempts o|..o{ opening_events : "logical attempt_id; no FK"
```

| Store | Stored fields / types | Keys and indexes |
| --- | --- | --- |
| `training` | `localDate: string`, `preparedAt: string`, `cards: BackendQueueCard[]`, `attempts: OfflineAttempt[]`, `nextTemporaryId: number`; full nested attempt/card definitions remain in [offline-training.ts](../../app/lib/offline-training.ts) and its imported domain schemas. | External key supplied to `put` (`RECORD_KEY` in the owning application); no inline key path. |
| `opening_attempts` | `attempt_id: string`, `manifest: OpeningDecisionManifest`, `origin_queue_entry_id: number`, `queue_entry_id: number or null`, `parent_attempt_id: string or null`, `started_at: ISO string`, `study_timezone: string`, `source: live/offline/reinforcement`, `terminal: {state, final_sequence, ended_at} or null`; plus `owner_session_id: string`, `final_sequence: number`, `delivery_state: idle/pending/complete/rejected/retained`, optional `rejection`, `retention_reason`, `local_capture_gap: string`, optional `delivery: {checkpoint, operationKey}`. | PK `attempt_id`; non-unique index `delivery_state`. The manifest contains its version/policy IDs, presentation snapshot, repertoire/card/revision/color and decision records. |
| `opening_events` | `attempt_id: string`, `sequence: number`, `decision_index: number`, `decision_id: string`, `expected_uci: string`, `kind: assistance/first_response/manual_failure/reveal/correction`, `observed_at: ISO string`, `response_uci: string or null`, `assistance: enum or null`, `disposition: enum or null`. | Composite PK `(attempt_id, sequence)`; non-unique index `attempt_id`. The attempt link is logical and has no referential constraint. |

Exact nested shapes: [opening-evidence.ts](../../app/domain/opening-evidence.ts). The mixed capture/delivery application remains in [opening-evidence-journal.ts](../../app/lib/opening-evidence-journal.ts); it was inventoried rather than split into a new architecture. Logical backend queue/card/attempt IDs in these records are not cross-database foreign keys. Browser storage must continue to work without Games availability.

Return to the [database index](../README.md).
