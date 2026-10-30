#!/bin/sh
set -eu
python -B -m app.product_composition.managed_runtime initialize
exec "$@"
