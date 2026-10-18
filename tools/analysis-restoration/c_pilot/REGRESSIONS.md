# Pilot owning suites

`tests/test_inputs.py` owns serialized A manifest/page identity, byte/digest/count
bounds, exact game selection, position history, source coverage and unknown
remediation delivery. `tests/test_stockfish_client.py` owns trailing completed
statuses, foreign execution rejection and ready-worker provenance fencing.

`integration/test_actual_consumers.py::test_C_actual_AB_consumers` requires explicit
`TEMPO_C_A_EXPORT`, `TEMPO_C_GAME_IDS_FILE`, `TEMPO_C_B_EXCHANGE` and
`TEMPO_C_ACTUAL_EVIDENCE`; it independently checks actual A pages and B legal PVs.
Missing artifacts fail, with no synthetic/live fallback. A's actual producer may
use explicitly synthetic games; this is C2, never C3 real-data acceptance.

`integration/study/test_study_off.py::test_C_optional_off_study` reuses F2's owning
Study assembly and requires an actual installed Intelligence shutdown receipt.
Run with `integration/run_study_off.py EVIDENCE STOP_RECEIPT`, or the explicit
`TEMPO_C_STUDY_EVIDENCE` / `TEMPO_C_OPTIONAL_STOP_RECEIPT` environment pair.
Component registration uses the existing Node/Python/PostgreSQL case accounting.

`qualify.py` builds wheel/sdist copies, removes the build source, and runs the
regular pure inventory against installed public artifacts on Python 3.12/3.14.

`tests/test_cli.py` owns PR295 R3 bounded partial-backlog configured/unconfigured
orchestration, startup failure and strict job-bound validation before startup.
The controlled dependencies are installed public packages. Existing installed
adapter tests cover session/trailing-status/provenance failures; actual qualified
B execution remains in the explicit native A/B lane.

## C3 real snapshot cohort

`tests/test_cohort.py` owns deterministic provider/color selection, duplicate and
conflicting provider identities, legal local-PGN reconstruction, and explicit
unknown opponent metadata. Local PGN hashes never claim original provider bytes.
`test_C3_shared_prefix_can_anchor_a_draft_only_with_exact_route_history` covers
multiple authored continuations at a genuine shared opponent decision. The old
adapter withheld every draft when more than one route survived. The consumer now
chooses a deterministic source route only after every context reconstructs the
actual ordered history, root, opponent turn and exact next learner FEN. A mismatched
context retains an unavailable draft; Content still owns inspection and approval.
The extension is gated to the new source metadata; older export normalization
retains its previous bytes. Context order cannot change the selected anchor.

C3 real-cohort serialization incident: normalization fit the consumer's canonical
byte budget, but the pilot wrote indented pages over Intelligence's unchanged
1 MiB CLI read limit. `test_C3_normalized_documents_fit_actual_consumer_file_limit`
reproduces that failure through the final file/consumer boundary.
`test_C3_oversized_normalized_document_rejects_before_consumer_start` and
`test_C3_consumer_document_bound_counts_utf8_and_newline` retain explicit rejection,
UTF-8 byte identity and the persisted newline. These are pure transport regressions;
the private 80-game installed-artifact retry supplies real acceptance evidence.
