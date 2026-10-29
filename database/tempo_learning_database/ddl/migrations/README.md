# Learning runtime migrations

[DDL owner](../README.md) · [Source schema charts](../../schema/README.md)

Existing ordered source DDL. Runtime startup and migration authority are unchanged. Use the owning documented explicit migration command; this PR does not run it.

Order/dependencies: [manifest.json](manifest.json).

- [0001_core.sql](0001_core.sql)
- [0002_evidence_revisions.sql](0002_evidence_revisions.sql)
- [0003_content_bridge.sql](0003_content_bridge.sql)

- [0004_retire_endgames.sql](0004_retire_endgames.sql) — explicit Endgames retirement; no live migration performed by this implementation.
