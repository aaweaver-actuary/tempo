# Intelligence source schema

Tables are grouped for navigation, not split into new runtime owners. [Database directory](../README.md). Every table has one canonical dictionary here.

| View | Tables | Stored data |
| --- | --- | --- |
| [Analysis](analysis/README.md) | `analysis_commands`, `analysis_dismissals`, `analysis_execution_events`, `analysis_executions`, `analysis_heads`, `analysis_inputs`, `analysis_pages`, `analysis_positions`, `analysis_report_items`, `analysis_reports`, `analysis_results`, `analysis_runs` | Analysis inputs and runs, positions/executions, immutable results/reports, command/event receipts and dismissals. |
| [Sources Evidence (1)](sources-evidence-1/README.md) | `caches`, `commands`, `credential_head`, `credential_registrations`, `evidence`, `instance`, `maia_jobs`, `migration_history`, `model_profiles`, `provider_control`, `request_heads`, `requests` | Provider requests, cache heads, accepted source evidence, credentials metadata and Maia jobs. |
| [Sources Evidence (2)](sources-evidence-2/README.md) | `snapshots` | Provider requests, cache heads, accepted source evidence, credentials metadata and Maia jobs. |
