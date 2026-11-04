# Frozen opening order

Previously published frozen opening ranks remain Study data. Runtime reads the
existing `frozen_opening_ranks` for the active run and preserves their stored
order; it does not calculate new Maia ranks. The former one-time generation and
publication tooling has been retired.

The disposable native proof in `database/tests/test-frozen-postgres.py` covers
rank serving, issuance, stale races, restart, rollover and rollback using
synthetic ranks. Historical evidence and provenance remain records of their
original runs and do not authorize new model execution or rank publication.
