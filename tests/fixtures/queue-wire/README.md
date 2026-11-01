# Queue wire compatibility fixtures

`producer.json` is generated from synthetic database rows by executing the current `_queue_payload`, `prepared_queue`, `queue_manifests`, and Learning `decision_manifest` functions. The generator extracts the route and manifest functions from their source AST to avoid importing the server composition root; its substituted reads, clock, and issuance are local fakes. This proves serialization compatibility, not database queries, issued-head persistence, or settlement. The unavailable case forces the optional evidence read to fail; the empty case returns a refreshing projection. Legacy absence and invalid fields are mutations of these generated payloads in the test.

The selected J1 proof includes the non-mutating freshness check below. It executes the current producer and fails if the committed fixture differs. Run it from the repository root using the backend Python dependencies:

```sh
PYTHONPATH=backend:database:services/learning/runtime/src:packages/scheduling/src python tests/fixtures/queue-wire/generate.py --check
```

Run `python -m pytest -q tests/fixtures/queue-wire/test_freshness.py` with the same dependencies to verify both freshness and drift detection. Remove `--check` to regenerate the fixture.

`prepared_at` is removed before queue computation, as in `savePreparedTraining`; the snapshot itself stays in the card. Run `npx vitest run tests/unit/queue-producer-wire-regressions.test.ts` for pure computation, and `node tests/fixtures/queue-wire/worker-proof.mjs` for the bundled production entry point in a real Chromium Worker. The latter needs the installed Playwright Chromium browser.

## E1 field and predicate manifest

Source pin `3fd16a58cf08cffac59fc8b3b0b940cb676bb8fc`. This is a
source inventory, not a proposed column deletion or proof of live issuance.
`queue_rows` currently carries **all `cards.*`**, not a finite owned select;
any replacement must account for its actual schema at the selected migration.

| Stage | Inputs or outputs that must be considered |
| --- | --- |
| Queue row | `q.id` as `queue_entry_id`, `position`, `cycle`, `attempt_state`, `attempt_failed`, `gameplay_priority_reason`, `admission_kind`, `admission_source`; review-derived `latest_review_id`, `latest_failed_review_id`, `has_study_review`, `study_review_count`; `cards.*`; selected `repertoire_name`, `repertoire_source`, `is_main`, `study_id`, `effective_trained_color`. Defense admission source may fall back to a candidate ID. |
| Core assembly and head | `moves_json` is parsed into `moves`, then removed; `effective_trained_color` becomes `trained_color`; `latest_failed_review_id` and `prefix_split_rejected_after_review_id` produce `prefix_split_offer_available`. Badge lookup uses `start_fen`. Issuance uses the day and first card's `queue_entry_id`, `id`, `revision` and refuses a changed/unissued head. Limited reads separately obtain `available_count`. |
| Opening manifest | Candidate `content_type`, `queue_entry_id`, `id`, `revision`, `start_fen`, `moves`; unique immutable context must match card and revision/FEN/moves. Snapshot `id`, `card_id`, `revision`, `start_fen`, `moves_json`, `trained_color`, `effective_trained_color`, `repertoire_id` feed manifest creation; context's `queue_entry_id` groups it. Ambiguous, invalid or missing context gives a diagnostic; a valid manifest supplies its immutable trained color and optional timezone. |
| Prepared exercise | `content_type`, `study_exercise_id`, `start_fen`; current exercise revision and `specification_json` produce `study_snapshot` with schema/grader versions, exercise ID, revision, FEN and specification. Missing current revision raises 409. `prepared_at` is a prepared-response field, removed by the offline consumer before queue decode. |
| Flat wire | Envelope `local_date`, `cards`, `count`, `diagnostics`, `projection`; refreshing fallback has state, generation, updated time, refresh pending, last error and blocked count. Card decoder requires nonempty `id`, positive `queue_entry_id`, `start_fen`, `moves`, supported `content_type`, `repertoire_name` and `repertoire_source`; `revision` is optional for legacy compatibility but, when present, must be positive. Other supported fields are enumerated by `queueCardSchema`: queue/attempt/review and admission facts, repertoire/study IDs, scheduling/card state and source fields, and the optional enrichment below. |
| Advisory/compatibility | Encounter status (`available` or `unavailable`), count, last-seen and badges come from a separate bounded read; absence of count on failure is not zero. Opening manifest and its diagnostic/timezone are optional; malformed manifest is removed with a diagnostic while preserving an otherwise valid card. Legacy absent status remains accepted; malformed status is currently rejected. `middlegame`, saved `endgame` and defense remain supported wire families. |

The current eligibility checks are intentional and remain unchanged:

| Predicate category | Existing required behavior |
| --- | --- |
| Core authority | Queue day and queued status; card not archived, superseded, deleted or pending validation. Opening has an eligible linked/direct repertoire without an integrity block; defense requires the user setting. Exercise is published, its Study is not archived, and non-explicit admissions respect same-day sibling burial. |
| Currentness and protection | Frozen definition/membership mismatch holds only unstarted, unprotected captured content. Miss priority, failed/reviewed/taught or already-issued attempts are protected by the existing predicate. The PostgreSQL legacy predicate holds uncertain unstarted legacy openings while a frozen run is active, with its explicit admission, outstanding miss, prior review/evidence/teaching and prior exposure exceptions. These are not optional badge predicates. |
| Count, window and issuance | `available_count`, `available_head` and the PostgreSQL bounded candidate window use `available_attempt_sql`; queue rows retain the corresponding filtering and `position,id` order. Actual head issuance checks the same entry/card/revision and refuses stale authority. A fixture stubbing storage and issuance cannot prove count/head agreement or protected-attempt eligibility. |
| Advisory | Games encounter lookup is a separate bounded read with failure reported as unavailable; it does not grant eligibility. Manifest context matching and prepared exercise revision are separate owner reads with the dispositions above. |

The generated protected `again` attempt, defense, saved endgame and middlegame
are synthetic row inputs through the real assembly; the storage and issuance
fakes do not establish their database eligibility or issuance.
