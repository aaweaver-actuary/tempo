# analysis-jobs contract regressions (#264)

Owning suite: `tests/test_wire.py` plus the subsystem reference test file.
Exact collected identities are reported by pytest; empty selection fails.

- `test_independently_authored_producer_round_trip`
- `test_exported_json_schema_matches_authoritative_model`
- `test_future_versions_and_unknown_fields_are_rejected`
- `test_every_public_interface_has_a_producer_example`
- `test_imports_resolve_to_installed_artifact`
- `test_non_json_numbers_and_coercions_are_rejected`

Behavior-specific tests document replay, authority, fencing and failure semantics.
The shared installed-artifact and dependency proof lives in `../tests`;
TypeScript consumers live in `../consumer-examples`.

## PR #271 timestamp correction

`tests/test_timestamps.py::test_installed_timestamp_validity_and_original_wire_spelling`
runs this package's own installed public envelope across 37 explicit validity cases,
including fractions, offsets, Gregorian leap rules, year bounds and malformed input.
Accepted strings retain their original offset and fractional representation.

`test_execution_microsecond_chronology_preserves_original_wire_spelling` covers
Stockfish and Maia results finishing one microsecond earlier/equal/later and at an
equivalent offset instant. No engine or model is started.
