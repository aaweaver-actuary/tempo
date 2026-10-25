# Physical source accounting — 2026-10-25

This receipt applies completed [#392](https://github.com/aaweaver-actuary/tempo/issues/392) tooling to [PLAN section 7](../PLAN.md#7-measure-actual-reduction). It establishes a comparable physical-line checkpoint, not semantic retirement, product acceptance or 50% completion.

- Frozen source baseline: `7d77ed1b25198dcd320e0265f777cc630f23775e`.
- Current `main` inspected and counted: `782bf5374b87f223a023568305056415d5b63cea` (includes #419).
- Original retained classification source: [MODULE-INDEX.md](../MODULE-INDEX.md), recovered from original audit-documentation merge `2c55dfab8004cba2fe3ab3b0497570f1609aaea9`.
- Index SHA-256: `907df27c2b2067882b2135e79a6854ae3e2fd2c3923de1770819b307cf6f4995`.
- Frozen [classification.json](./classification.json) SHA-256: `a1f86e5cf16e70d1d383eb1ea3d24b73780baf7309540fa2d4a38c8d43e6311e`.

## Reconciled production totals

Every one of the 881 unique retained index rows was checked against its baseline Git blob: zero missing paths, duplicates or line-count mismatches. Narrow membership is the index's `backend/app/*.py` plus database Python/SQL subset, including nested modules. This recovers the reported 234 backend files / 40,133 lines and 363 database files / 29,553 lines. Broader production includes narrow production; the two rows must not be added together.

| Comparable scope | Baseline files / lines | Current files / lines | Net physical lines | Baseline decrease |
| --- | ---: | ---: | ---: | ---: |
| Narrow historical backend/database | 597 / 69,686 | 594 / 69,309 | −377 | 0.541% |
| Broader historical scope plus new runtime source | 881 / 97,217 | 855 / 94,646 | −2,571 | 2.645% |

The surviving historical broad cohort alone is 854 files / 94,551 lines. The new production module `services/content/src/tempo_content/import_manifest.py` adds 95 lines, giving 94,646. Omitting it would overstate the comparable reduction by 95 lines. Its actual CLI/bridge role, and the tooling-only contracts of new disposable/census/snapshot modules, were inspected. No other added protected runtime source was found.

[production-changes.tsv](./production-changes.tsv) provides all 38 changed production paths, including the new module. Their net changes reconcile as follows:

| Source area | Net lines |
| --- | ---: |
| Narrow backend/database, including +8 Learning connection lines | −377 |
| Browser database adapters/cache already in the broad ledger | −134 |
| Content bridge/CLI/new manifest | +136 |
| Learning runtime API | +26 |
| Maia historical broad members | −951 |
| Stockfish historical broad members | −1,271 |
| **Broad inclusive net** | **−2,571** |

The original narrow 50% target remains **34,843 net physical lines**. This checkpoint leaves **34,466 further comparable narrow lines** to remove. Broad worker/frontend reductions cannot be substituted for that narrow target. This is an arithmetic planning gap, not a list of safe deletions or authority to remove protected Study/history code. #390 candidate preparation remains with its separate owner.

## Historical classification inconsistency

The original broad ledger includes nine clear build/test/benchmark/qualification helpers despite [AUDIT.md](../AUDIT.md#1-the-measured-baseline) excluding such helpers in prose. They are the four Board UI build/consumer/qualification/config files and five Maia/Stockfish benchmark/build/qualification files explicitly listed in `HELPERS` in [reproduce.py](./reproduce.py). Their frozen broad membership is preserved: **733 baseline lines → 298 current lines, net −435**.

For sensitivity only, excluding these same nine paths at both ends gives **96,484 → 94,348, net −2,136**. That is a differently scoped measure, not a correction silently substituted for the original 97,217 denominator. The reported broad −2,571 includes those 435 helper lines and must not be described as an equal amount of runtime feature retirement. Narrow totals are unaffected.

## Tests, docs, generated evidence and other source

The frozen manifest contains 1,262 unique paths: all 881 historical production members plus 381 supplemental text paths. [supplement.tsv](./supplement.tsv) supplies explicit per-path categories, kinds and reasons. Review corrected authored pytest/config inputs and hand-maintained inventories so they are not called generated output. Supplement rows never relabel original membership.

All **437 baseline-to-current changed paths** are covered once: 37 historical ledger paths, 381 supplemental text paths and 19 binary test fixtures. Unlike the production rows above, the following totals cover **changed text paths only**, not complete repository test/doc/tool denominators. Generated artifacts have their own kind inside the tool's `other` category. Outside-audit browser runtime is separate from broader production, preserving the original six-root scope.

| Supplemental kind | Before lines | After lines | Net lines |
| --- | ---: | ---: | ---: |
| Test assertions, fixtures and test support | 26,013 | 19,577 | −6,436 |
| Prose/licenses | 16,255 | 19,217 | +2,962 |
| Generated qualification/observed evidence | 19,350 | 0 | −19,350 |
| Authored inventory/registration metadata | 26,587 | 27,387 | +800 |
| Offline evaluation corpus/synthetic input data | 9,055 | 0 | −9,055 |
| Other offline/build/disposable tooling | 4,158 | 3,022 | −1,136 |
| Build/package/lock configuration | 3,034 | 2,800 | −234 |
| Browser runtime outside the historical audit scope | 20,425 | 9,383 | −11,042 |

The new 95-line Content runtime module is the remaining supplemental kind and is already included in broad production. None of these excluded-source reductions contributes to either audited production target. [binary-fixtures.tsv](./binary-fixtures.tsv) records **19 removed screenshot fixtures / 2,109,457 bytes**; binary files have no invented physical-line counts and are intentionally omitted from source_delta.py's text manifest.

## Moves and limits

Git rename detection at 50%, 20% and 10% similarity finds no renamed paths. No deleted audited production blob survives at any current repository path by exact Git blob ID. The demonstrated whole-file move adjustment is **zero**, so move-adjusted physical net remains narrow **−377**, broad **−2,571**. Both tests search across all repository paths, not just the historical roots. A 1% exploratory review produced false rename pairs and was not accepted as evidence of movement.

These checks do not certify absence of partial extraction, copying, algorithm rewrites or changed responsibility boundaries. The line method includes blanks/comments; it does not allocate mixed diffs into a proven semantic-retirement amount. Formatting-only and comment-only savings must not be claimed as functional removal. Existing reviewed retirement cohorts in [RETIREMENT-BATCHES.json](../RETIREMENT-BATCHES.json) retain their own safety/acceptance evidence; this receipt neither expands those authorizations nor certifies a new deletion cohort.

The original raw CSV/ZIP and the 43-entry methodology-correction file were not recovered from the bounded accessible task-directory/source search. Original #356 states they remain outside the active source tree and supplies no raw artifact location. Exact retained path membership and blob sizes are recovered from its committed index, but the unavailable correction rationale is not claimed independently re-audited. This is an explicit provenance gap, not an unknown numerical denominator.

Runtime behavior, actual device/database authority, history mapping, launcher/cutover, table retirement and deployment remain outside this source receipt. The historical audits and programme requirements are unchanged.

## Reproduction and validation

Use any checkout containing the three pinned commits and unchanged #392 tool. No network, dependency install, service/database access or application test execution is required. From its repository root:

```sh
python3 docs/architecture/study-first-reduction-2026-10-21/source-accounting-2026-10-25/reproduce.py --output /tmp/tempo-accounting-repro
```

The receipt script reads the original index from Git, validates all baseline counts and changed-path coverage, checks binary bytes/moves, writes the deterministic classification/summary/production table, and invokes the existing tool unchanged:

```sh
python3 tools/component-build/source_delta.py --base 7d77ed1b25198dcd320e0265f777cc630f23775e --head 782bf5374b87f223a023568305056415d5b63cea --classification /tmp/tempo-accounting-repro/classification.json
```

Retained outputs are [delta.json](./delta.json), [summary.json](./summary.json), classification.json and production-changes.tsv. The tool itself honestly labels totals as explicitly classified paths; the independent baseline reconciliation above establishes comparability for the production subset only. The report uses LF bytes plus one for a nonempty file without a trailing LF; empty files count zero. No binary or unreadable source was assigned a line count.

Executed in the owned temporary checkout, with the receipt directory as `--output`: final reproduction exited 0. All baseline/source/coverage/binary/move assertions passed; its unchanged source_delta.py exited 0. Its SHA-256 is `ef2ca42d1636e8d56ce76db99ee015c889a5c7eb088db7dfc8a87d9f1660c2cf`; no reproduced correctness defect justified editing it. An earlier provisional output was superseded after independent review corrected two nonproduction labels.

[Independent review](./REVIEW.md) accepted the corrected classifications, denominator reconciliation, additions and whole-file move checks, and states their limits. No #392 tool suite, unit, build, native/browser, Compose or runtime tests were run for this documentation/source-accounting task. Documentation conflict/whitespace and output integrity checks are recorded in the PR. Architecture impact: none; these are historical source measurements and evidence, with no runtime interface, dependency, lifecycle or authority change.
