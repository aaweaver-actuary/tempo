# Opening evidence application

Existing opening-evidence policy and composition now live in [this module](__init__.py). It contains the original manifest/color validation, matching/fallback, immutable checkpoint/event/terminal decisions, reduction/summary deltas, prepared DTO/plan, publication staleness checks and response mapping. Public function signatures and wire behavior remain; there is one implementation.

Complete SQL, locks, driver identities and managed read sections stay in the single root-owner [database adapter](../../../../../../database/tempo_learning_database/adapters/postgres_opening_evidence.py). The canonical bounded [ownership drawing and source-schema links](../../../../../../database/tempo_learning_database/adapters/opening-evidence/README.md) document that boundary. Existing API/review composition calls this application module. Readers close before compute; writers still borrow the original caller transaction and preserve aggregate review/evidence atomicity. No new model, framework, generic execution port or queue/settlement authority was added.

Source/schema and deployment claims are separate. The focused checks do not establish native PostgreSQL locking, rollback, races or resource isolation. Standalone prepare/commit functions are retained despite unresolved caller usage. Runtime SQL compatibility delegates elsewhere are unchanged.
