# Content final generation ledger

The additive `0005_content_final_generations` migration owns this ledger in the Content database. The reservation protects a single Content source generation from supported writers; it is not a legacy-source watermark, an administrator-bypass fence, or a target activation receipt. The capture stores original immutable bytes; no retention cleanup is defined here.

```mermaid
erDiagram
    SOURCES ||--o{ FINAL_GENERATION_RESERVATIONS : protects
    BRIDGE_OPERATIONS ||--o{ FINAL_GENERATION_RESERVATIONS : approves
    FINAL_GENERATION_RESERVATIONS ||--o| FINAL_GENERATION_CAPTURES : stores
    FINAL_GENERATION_AUTHORITY { smallint singleton PK
        uuid database_authority_id UK }
    FINAL_GENERATION_RESERVATIONS { text reservation_id PK
        text source_id FK
        text workspace_id
        text transfer_id
        bigint epoch
        text publication_id
        bigint generation
        text approved_operation_id FK
        text approved_draft_digest
        char64 request_digest
        text state
        text capture_id
        jsonb resolution_json
        timestamptz created_at
        timestamptz updated_at }
    FINAL_GENERATION_CAPTURES { text capture_id PK
        text reservation_id FK,UK
        bytea certificate_bytes
        bytea export_bytes
        bytea inventory_bytes
        bytea proof_bytes
        char64 certificate_digest
        char64 export_sha256
        char64 inventory_sha256
        char64 proof_sha256
        bigint export_byte_length
        timestamptz captured_at }
```

`final_generation_authority` contains one migration-owner-generated logical database UUID. A copied database retains this UUID; a separate instance/connection qualification is still necessary. Runtime may only select this table. Reservations have unique `(source_id, epoch)` and `(source_id, transfer_id)` and at most one active row per source. Epochs are source-local and increase under the source lock. Runtime can select, insert and update reservations but cannot delete them. A capture has one reservation and immutable certificate/export/inventory/proof bytes and hashes. Runtime has insert/select only; an owner-installed trigger rejects capture update/delete. Exact types, checks, indexes, grants and triggers are in the migration and migration owner.

Reservations move from `reserved` to `captured` and then `accepted` or `aborted`. `recovery-held` remains an active writer fence. Resolution uses an injected, owner-qualified read-only target-disposition port outside the Content transaction; a missing or uncertain disposition keeps the original reservation. No expiry or browser lifetime releases a fence. Target freshness, actual receipt authority and legacy-source correspondence require their own owner proof.
