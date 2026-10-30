#!/bin/sh
set -eu
# Optional Games identity failure must not prevent ordinary backend execution.
# Reads also bind the nonce to the current PID1 start, rejecting any stale file.
python -B -m app.product_composition.managed_runtime initialize || true
exec "$@"
