# Feedback boundary integration qualification — 2026-10-20

This is a committed summary of private raw qualification receipts, not a claim
that the raw logs or artifacts have been uploaded. Counts and hashes below refer
to the completed qualification, not a rerun of this documentation-only publication.

## Qualified source and delivery

- Tested commit: `87047f062c23b97939fa062d386b82f4d03f223e`.
- Tested Git tree: `82ebfe0b4d403d9225b10e78c896658fed8b7984`.
- Base main included: `45aeb03e55e96b2719e02a09a5f4fa34fdabb688` (including PR315 guidance).
- PR311 bridge input: `501f25e2da29ebf6a555cfd1137facab50612d0d`.
- PR313 Discovery input: `215f8a27132ad2ad4e2efe46109eb6a5b687e7d1`.
- PR314 queue executable input: `bd672bd6f8e34026270775e0010323e46d8f9005`.
- PR316 feedback input: `e7e8722e845759dd4b6ee258d48f163cb4d20acd`.

The integration PR targets main and is the assembly delivery vehicle. It contains
all these component inputs plus the integration changes. In particular,
`backend/app/services/queue_admission_adapter.py` imports
`outstanding_miss_query` from the storage projection and
`has_outstanding_real_game_miss` from the composition adapter, retaining only
`MISS_REASON` from the policy. These two rebindings must be delivered together
with the component changes; merging only the component PRs does not supply them.

Component heads remain independent and unchanged by this publication. Review and
accept PR311, PR313, PR314 and PR316 as dependencies before accepting the assembly.
PR316 is based on PR311. If the components merge separately, refresh/review the
assembly diff against main and retain the two rebindings; alternatively the
assembly can deliver the accepted component histories together. Do not treat the
assembly and component PRs as unrelated patches to apply repeatedly. Any changed
executable assembly requires qualification appropriate to that change.

The publication commit adds only this receipt to the tested tree. No executable,
fixture, dependency, schema or qualification guard change accompanies it. No merge
or deployment is authorized or reported here.

## Completed focused proof

- Source: 158 tests plus 9 subtests passed.
- Queue/Discovery owner checks: 58 passed with a guard rejecting socket
  connections/binds and SQLite connections.
- Fresh artifacts at the tested commit: 284 passed: wire-v3 17, remediation 85,
  game-evidence 125, bridge 57. Six wheels and six sdists were produced; all nine
  producer source directories were removed for installed checks. Three current
  backend dependency wheels were verified byte-for-byte.
- Native: six collected, six executed, six JUnit passes; zero failures, errors or
  skips. Exact identities:

```text
integration/legacy_bridge/test_native.py::test_native_generated_accept_replay_restart_and_normal_study
integration/legacy_bridge/test_native.py::test_native_prior_issued_attempt_cannot_settle_later_acceptance[prior]
integration/legacy_bridge/test_native.py::test_native_module_effect_and_complete_operation_receipt_roll_back_together
integration/legacy_bridge/test_native.py::test_native_feedback_typed_projection_and_priority_effect_borrow_caller_transaction
integration/legacy_bridge/test_native.py::test_native_feedback_card_and_day_locks_live_until_caller_transaction_ends
integration/legacy_bridge/test_native.py::test_native_feedback_accepted_scope_exclusion_does_not_hide_other_repertoire
```

The native tests use synthetic games and actual PostgreSQL/Redis/HTTP/Celery
boundaries. They cover generated public capture/acceptance, canonical queue and
Study settlement/replay, rollback, borrowed transaction lifetime, typed bounded
projections, advisory/row locks, and repertoire-scope exclusion through the actual
combined queue projection. They are not a current private-game cohort, browser,
full-system, hosted-CI or deployment proof.

## Artifact and raw-receipt provenance

All hashes are SHA-256 of retained private files; filenames identify the receipt
within its qualification run and do not imply public download availability.

| Receipt | SHA-256 |
| --- | --- |
| Artifact run `qualified-inputs.json` | `9aea2f816285dee2885451752d1f2fcb719f4f10faefc454b086f5cea7d80a79` |
| Native `host.json` | `7f16d464f90970290ca61dc1d0649251c214a89d96c0762f189c44bb5f47ec6c` |
| Native `inside.json` | `8f8a75f6eef8ef9cb575ccfb3797828318ee1b68628b0648cca6a55ba07a8269` |
| Native `native-collection.json` | `9db56965229b7c99de6b9b16b6b549c3a04f24d28f19120724ab4db94444a17c` |
| Native `native-execution.json` | `930f3248c715da350e61139cc67ff46f5f77a12f0d8fc66eaf248e43df9bc4ff` |
| Native `native.xml` | `207add28c6f2776960ee9902ccaad2ffc0c2a5d88045186d4d480fa6b2b1d74a` |

Artifact run: 2026-10-20 11:44:26.982061–11:44:53.600696 UTC.
Native run: 2026-10-20 11:46:20.110481–11:47:52.142129 UTC.
Pinned local linux/arm64 images (no image pull/build):

- Helper: `sha256:a095f09e905ecb00635b80387e17d195560a5752df83d970f94f1ada902ba3e2`.
- PostgreSQL: `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
- Redis: `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499`.

## Resource, cleanup and acceptance limits

Observed helper limits: 1 CPU/768 MiB/128 PIDs; PostgreSQL: 0.75 CPU/384 MiB/128
PIDs; Redis: 0.1 CPU/64 MiB/32 PIDs. Aggregate: 1.85 CPU/1216 MiB/288 PIDs,
no extra swap, internal network, no host ports/live mounts/persistent volumes.
Owned labels and actual caps were inspected before completion.

Independent cleanup verification at 11:50:17.608144 UTC found all three exact
containers, their internal network, process groups, staging and budget lock
absent. No persistent volume was created. Cleanup preceded the native hard
cutoff 12:06:20.110481 UTC and parent cutoff 12:13 UTC. Live Study was untouched;
read-only availability checks stayed healthy. No further qualification runtime
is retained by this handoff.

The earlier native attempt retained a fixture failure (3 passes, 1 failure,
2 unreached): its helper inserted into a read-only publication view. The repair
writes the staged event plus completed job and asserts the published view, with
a named red/green regression. Product behavior, schema, assertions and timeouts
were not weakened. The fresh six-case pass above supersedes neither nor erases
that failed attempt. Earlier source/import failures also remain in private logs.

This proves the changed semantic boundaries and their actual compositions.
It does not establish global architectural cleanliness: the pre-existing
`legacy_study_remediation.repository` dependency on the command-root borrowed
connection remains an explicit deferred legacy boundary. Required merge checks,
independent final review, integration acceptance and deployment remain separate.
