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

Route-scoped mapping tests cover an unmapped transposed occurrence, attempted
reentry with another route's binding, and multiple active authored occurrences.
`test_introduction_assertion_applies_only_to_its_exact_game_source` rejects scoped
proof reused across providers, games, source revisions or digests. The pure policy
test `test_explicit_game_scoped_introduction_preserves_missing_timestamp` covers
positive/negative/unknown assertions, unchanged-content and occurrence-precision
guards, and conflicting timestamp evidence without fabricating a timestamp.

Next-release encounter regressions keep played/expected/source facts for correct
and different unintroduced play, continue covered traversal after a correct
encounter, preserve a later gap and authorized reentry, refuse invented card
authority, and preserve the event key after later introduction. An incorrect
historical encounter remains importance evidence without an FSRS lapse; a correct
one remains frequency evidence without failure. Current Study disposition is not
selected by these tests or this component.

`test_game_encounter_contract_preserves_classified_facts` verifies direct
compatibility with the frozen 0.2.1 `GameEncounter` for matched/missed observations
and unintroduced/later-introduced current state. It preserves the exact source,
one-based key occurrence, route-local mapping, original detection/occurrence time
and caller values. It does not implement command delivery, owner disposition or
receipt/admission policy.
