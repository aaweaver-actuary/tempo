# GitHub workflows

`pages.yml` classifies PRs into FAST, STUDY or FULL and reports the required
`merge-gate`. FAST/STUDY use exact-head focused execution; FULL delegates to the
existing verification layers and `quality` integration validator. The selected
manifest, evidence policy, coverage gaps and required GitHub protection settings
are documented in `docs/testing.md`.

Complete verification runs nightly at 07:00 UTC, on manual dispatch, release,
merge-group and main publishing boundaries. Schedules and verification-only
manual runs cannot deploy. GitHub Pages remains a practice demo, separate from
local Docker Tempo. No CI runner uses live study data.

All tiers reject draft PRs and draft-selected plans. FULL merge aggregation binds
the same-run immutable complete plan to the current integration SHA; `quality`
retains responsibility for complete layer/evidence validation. Sensitive Markdown
prose remains FAST; executable security and qualification changes retain FULL.
See [testing policy](../../docs/testing.md#emergency-merge-qualification) for
ownership limitations, base-advancement risk, rollout status and rollback.
