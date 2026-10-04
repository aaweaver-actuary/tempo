#!/bin/sh
# Compatibility entry point; all checks live in the Tempo CLI.
set -eu
tempo_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$tempo_directory/tempo" restart "$@"
