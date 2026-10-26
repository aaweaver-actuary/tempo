# Classification regression ownership

Regular suite: `python -m pytest tests --import-mode=importlib -q`.

`test_stub_is_explicitly_unavailable_without_touching_input` proves that the stub returns `not_implemented` and never inspects or executes the supplied request. This is not classification/action implementation proof.

`tests/test_policy.py` owns introduced-before-game chronology, unknown time
precision, unavailable/ambiguous mappings, changed content, own-response conflict,
opponent gap versus missing own continuation, intentional coverage end, correct
response, and caller-container preservation. These are pure supplied-fact checks;
they do not prove a live snapshot or learning effect.

The regular backend suite `backend/tests/test_product_game_findings.py` owns
normalization for both providers, original source identity/FEN/history/time,
illegal/unsupported rejection, digest fences, lapse versus gap/learning need,
no-cascade, index-authorized exact reentry, unchanged earlier findings,
terminal coverage, conflicts anywhere in one repertoire, ambiguous repertoire
and physical mapping, stable logical effect keys under re-comparison/source
correction, caller-container preservation and denied infrastructure imports/calls.
It uses the shared product DTOs and a fake immutable-index capability. Shared
availability and current-content introduction proof default to false; supplied
proof is required before lapse eligibility.

`test_canonical_game_without_original_pgn_preserves_provenance` owns #488's
retained-history boundary: canonical game comparison never calls the PGN
ingester, preserves exact source/game identity, revision, digest, history and
input values, and keeps unspecified occurrence precision unavailable for lapses.
