# Packaged Games collection qualification

This is the explicit `games-collection-release` shared suite in `components.json`.
It owns synthetic SQL setup/readback for the collection release and reuses the
existing `test_product_game_lapse_postgres.owned_postgres` fixture for ordered
migrations, resource identity and exact cleanup. No second migration or database
lifecycle owner is created. The provider HTTP fixture stays with backend
composition tests; production modules come exclusively from the pinned image.

Run through `node tools/component-build/test-lane.mjs --suite games-collection-release`
with `TEMPO_COLLECTION_TEST_IMAGE` set to an immutable image ID and the supported
backend interpreter selected by `TEMPO_COMPONENT_PYTHON`. An explicit
`TEMPO_COLLECTION_TEST_REVISION` may name the original image source when every
Dockerfile COPY input still matches; no image identity is relabeled. Missing or
mismatched inputs fail this explicit suite. General backend test discovery does
not implicitly build or require a packaged release image.

See the [qualification receipt](../../../docs/validation/task-g-collection.md)
and [operator boundary](../../../scripts/README.md#games-collection-operator).
