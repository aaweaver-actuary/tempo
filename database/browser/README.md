# Browser persistence

Current source definitions; existing browser data/version state was not inspected. Owner: the Study/browser client. These are IndexedDB object stores and serialized JavaScript records, not PostgreSQL tables. Key paths are enforced by IndexedDB; field shapes are TypeScript/runtime validation contracts. IndexedDB declares **no foreign keys**.

| File | Data / authority |
| --- | --- |
| [tempo-db.ts](tempo-db.ts) | `tempo`, version 1; portable snapshot records in ten stores below. |
| [offline-training-storage.ts](offline-training-storage.ts) | `tempo-offline-training` or the workspace-specific name from `study-connection`; version 2; prepared queue and incremental opening journal stores. |
| [study-intent-storage.ts](study-intent-storage.ts) | Browser intent package adapter and legacy localStorage cutover/checkpoints; uses the existing [intent package](../browser-intents/README.md). |
| [study-connection-storage.ts](study-connection-storage.ts) | Existing read-only retained-store inspection, tab connection session and persistent authority-registry reads/writes; connection/transport decisions stay in the Study application. |
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

## Connection session and retained authority registry

These are existing Web Storage key/value records, not SQL tables. The [storage helper](study-connection-storage.ts) owns reads/writes and retained IndexedDB inspection. [Study connection composition](../../app/lib/study-connection.ts) retains origin/workspace/bootstrap validation, authority-transition decisions, request generations and transport/error handling. It supplies registered offline database names through a narrow callback; storage inspection invokes it at the original point after checking IndexedDB availability. The helper imports no application implementation.

```mermaid
flowchart LR
  application[Study connection decisions and composition] -->|existing storage operations; registered-name callback| storage[Browser connection storage helper]
  storage -->|read-only retained inspection| indexed[IndexedDB stores]
  storage -->|existing reads and writes| web[Session and local Web Storage]
```

```mermaid
erDiagram
  connection_session {
    string storage_key PK
    string origin
    string workspace
    string credential
    object bootstrap
  }
  authority_registry {
    string storage_key PK
    object bindings_by_identity
  }
```

| Storage / exact key | Stored data and logical relationships |
| --- | --- |
| `sessionStorage`, `tempo-learning-connection-v1` | JSON connection with `origin: string`, `workspace: string`, `credential: string`, optional `bootstrap: StudyBootstrap` (schema version, workspace, timezone, local date, settings). Tab-scoped restore state; the credential is not included in retained-data inspection/export. The application retains the existing runtime validation. |
| `localStorage`, `tempo-learning-authorities-v1` | JSON object keyed by encoded authority identity; each binding stores `origin: string`, `workspace: string`, `offlineDatabase: string`. The identity and database-name relationship is validated by the application; there is **no foreign-key constraint**. No credential is stored in this registry. |
| Existing legacy pending-operation keys in `localStorage` | The existing inspection reads nonempty values matching `tempo-pending-*`, `tempo-bury-operation*`, `tempo-bury-identity*`, `tempo-seen-moves*` and `tempo-first-clean-passes*`; values remain the original serialized strings. Exact matching stays in the linked helper. Inspection does not rewrite, delete, import or upgrade them. |

The read-only inspector enumerates known/discovered `tempo-offline-training:*` databases plus `tempo-study-intents` and `tempo-offline-training`, retaining the existing version/key/index checks. Application decisions about retained/foreign authority data stay outside this directory. This relocation changes no stored names, payload formats, schema versions, credential handling or live browser data.

Return to the [database index](../README.md).
