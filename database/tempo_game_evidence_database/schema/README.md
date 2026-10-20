# Game Evidence source schema

Tables are grouped for navigation, not split into new runtime owners. [Database directory](../README.md). Every table has one canonical dictionary here.

| View | Tables | Stored data |
| --- | --- | --- |
| [Observations Delivery (1)](observations-delivery-1/README.md) | `accepted_heads`, `analysis_export_pages`, `analysis_exports`, `comparisons`, `evidence_schema_migrations`, `game_sources`, `games`, `import_cursors`, `import_pages`, `jobs`, `outbox`, `provider_leases` | Imported game source bytes and documents, snapshots, comparison revisions, current heads, reconciliation and delivery outbox. |
| [Observations Delivery (2)](observations-delivery-2/README.md) | `snapshots`, `workspace_inputs` | Imported game source bytes and documents, snapshots, comparison revisions, current heads, reconciliation and delivery outbox. |
