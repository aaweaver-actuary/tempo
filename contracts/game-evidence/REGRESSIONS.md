# game-evidence contract regressions (#264)

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

## V2 correction and chronology (#41 / #230–232)

`tests/test_v2.py` owns independently authored v2 round trips/schema parity,
withdrawal-only deliveries, required nullable occurrence time, explicit
encounter/miss/publication/revision linkage, list bounds, and rejection of
implicit withdrawals, inconsistent references and cross-version negotiation.
Existing v1 fixtures, schema and producer output remain unchanged. The installed
Learning remediation suite owns semantic validation against authoritative content;
contract fixtures alone do not establish production adoption or supported misses.
