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
