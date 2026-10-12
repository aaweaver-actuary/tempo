# PR191 integration qualification

Base 3f7332399796366eb32f9fcb5e1b5c678a5b1562; original head 3b2e9ec262bb2427a805af46711bbe1de1f59df8. Only textual conflict: append-only tests/REGRESSIONS.md; preserve both complete sections.

Risks: joined continuation loss, earlier confirmation loss on follow-up rejection, error attribution/shared observer mutation, synchronous public-event ownership, false persistence, duplicate scheduling, unauthorized successor, guidance marker regression, immutable identity recovery.

Proof: 39 flush_settlement_audit unit cases; whole four settlement files; existing queue/lifecycle/failure-marker/Home studies and saved-move verification boundaries; typecheck/lint/conflict checks. Native full post-review-progression browser file includes overlap/restart/receipt replay. CI owns complete exact head/base/integration qualification via ci:full, including all PostgreSQL durability and native queue-attempt recovery/training-transition proofs. No duplicate local full gate.

No public API, storage/schema, backend, queue architecture, or session extraction changes. Existing baseline failure evidence preserved in original audit; no need to recreate five established baseline failures.
