# Architecture source maps

The November 10 aggressive reduction pass 2 narrows the implemented source to Study, Tactics, repertoire editing/import and minimal repertoire-based Game Review. Earlier diagrams are recoverable in Git history; they do not describe this candidate.

- [Application, storage and lifecycle ownership](system.md)
- [Study and minimal Game Review](study-games.md)
- [Transaction and recovery flow](flows.md)
- [Machine-readable reduction audit](../reduction-pass2/README.md)
- [Preserved historical schema dictionary](../schema-history/README.md)

These maps describe source ownership. Native PostgreSQL qualification, merge, installation and activation are separate states. Preserve the original transaction/receipt identities and backup safeguards when qualifying or deploying.

Update the relevant map whenever a change materially alters dependency direction, durable state ownership, transaction boundaries or lifecycle isolation. Verify the owning source and registrations, use a bounded diagram, and state unverified deployment limits. Historical source diagrams belong in dated history rather than being presented as the current runtime.
