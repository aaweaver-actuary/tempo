# Legacy backend: Authored Content (3)

Repertoire/line/membership and annotation source data or related authored-content projections.

**Current source schema, deployment unverified.** Owner: Legacy backend; SQL schema `public`. Derived from checked-in DDL at the `14ad5754` baseline with file relocation only. No live database was queried. [Database index](../../README.md).

Charts show primary/foreign key columns; the dictionary lists **every stored field and its SQL type**. `PK` means primary key (all marked fields form one composite key); `FK` means an enforced SQL foreign key. Parent `||`/`o|` means a required/optional parent; child `o{` means zero or more children and `o|` zero or one (a unique referencing tuple). Edges describe allowed SQL cardinality, not an obligation to create children. Nullable components of a composite FK can leave the tuple unlinked under PostgreSQL MATCH SIMPLE. No edge is inferred from a similar field name.

```mermaid
erDiagram
  repertoires {
    text id PK
  }
```

## Data dictionary

### `public.repertoires`

Primary key: (`id`). DDL: [001_initial.sql](../../ddl/migrations/001_initial.sql), [029_repertoire_daily_limits.sql](../../ddl/migrations/029_repertoire_daily_limits.sql), [033_canonical_prefix.sql](../../ddl/migrations/033_canonical_prefix.sql).

| Field | SQL type | Null allowed | Keys |
| --- | --- | --- | --- |
| `id` | `text` | no | PK |
| `name` | `text` | no |  |
| `source_name` | `text` | no |  |
| `created_at` | `text` | no |  |
| `is_main` | `bigint` | no |  |
| `new_cards_per_day` | `bigint` | yes |  |
| `canonical_prefix_moves_json` | `text` | no |  |
| `canonical_prefix_revision` | `bigint` | no |  |
| `canonical_prefix_preview_id` | `text` | yes |  |
| `scope_source_revision` | `bigint` | no |  |
