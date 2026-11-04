# Historical authored origin receipts

Source candidate; migration061 is additive source only, never a live apply.
The legacy authored-source owner captures one actual read-only repeatable-read
snapshot, retains exact semantic facts and original accepted-digest domain,
and inserts a sealed historical observation in a borrowed writer after the
existing authored and owner-epoch fences. Ordinary source preparation does
not serialize the archive; retention is explicitly opt-in.

```mermaid
flowchart LR
  reader["Owner RO/RR snapshot"] --> prepared["Sealed historical source facts and occurrences"]
  prepared --> writer["Borrowed writer: authored and epoch fences"]
  writer --> receipts["Immutable historical origin receipt"]
  retry["Exact prepared-byte retry"] -->|before new currentness checks| receipts
  receipts -. "Content authority scope absent" .-> blocked["No Content output; activation ineligible"]
```

`public.legacy_content_historical_origins` key is
`(legacy_target_id, legacy_workspace_id, export_id)`; `receipt_id` is unique.
`payload` BYTEA retains the versioned semantic observation, original token and
ordered repertoire/route/ply occurrences; `receipt_digest` binds its exact bytes.
UPDATE/DELETE/TRUNCATE are rejected; insertion does not rewrite earlier evidence.
The namespace is the existing logical backend identity, never physical-instance
proof. Fresh preparation includes new snapshot observations and is not claimed
to reproduce prior prepared bytes after service recreation.

Every observation is historical-repeatable-read, physical-currentness and
physical-instance UNQUALIFIED, activation ineligible and has no final legacy
fence receipt. Cross-repertoire authority anchor is unavailable: Content source
and manifest bytes/hashes are absent and projection is BLOCKED_AUTHORITY_SCOPE.
An exact accepted retry returns the original receipt even if authored state
subsequently changes; a new insertion must still pass its actual owner fences.
No scheduling, grade, queue, current correspondence or installed authority is
created. Full legacy physical fencing and Content approval binding remain gates.
