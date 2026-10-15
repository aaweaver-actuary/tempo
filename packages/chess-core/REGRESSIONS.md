# Identity regression ownership

Regular owning suite: `uv run --locked --no-editable python -m pytest tests`.

- `test_exact_baseline_digest_and_fen_key`: 90 deterministic pre-extraction cases.
- `test_existing_python_rust_digest_fixture_is_preserved`: eight unchanged shared cases.
- `test_clocks_and_whitespace_do_not_split_identity`.
- `test_castling_en_passant_case_and_move_order_remain_distinct`.
- `test_malformed_and_empty_input_are_hashed_without_repair`.
- `test_installed_identity_package_exports_only_identity_without_runtime_dependencies`.
- `test_installed_identity_runs_in_fresh_process_without_service_or_engine_dependencies`.

Legacy delegation and shared-fixture synchronization are owned by
`backend/tests/test_scheduling_package_adapter.py::test_legacy_identity_adapter_preserves_shared_digests_and_public_identity`.
No persistence or chess-engine claim moves here.
