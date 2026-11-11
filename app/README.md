# Browser application

Home owns the retained Study presentation and uses the public
[`features/study`](features/study/README.md) controller: a read-only snapshot and
named actions coordinate the existing single Study session, outbox and durable
journals. Original review and attempt identities survive retries; a pending save
does not advance the attempt. Unmount and supersession fences protect progression.

Queue acquisition reads the current flat queue and handles the original pending
issuance operation, holds and unavailable responses. Only explicit
`foreground_wait` is treated as that admission condition. There is no optional
prepare-next or feedback preparation request. A playable successor remains an
assembled-runtime acceptance, not an implication of a controller test.

Tactics, repertoire editing/import and minimal Game Review retain their own
views. The [source maps](../docs/architecture/diagrams/README.md) describe the
current ownership and qualification boundaries.
