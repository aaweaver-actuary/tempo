#!/bin/sh
# Compatibility entry point; invoking this command authorizes checked maintenance.
set -eu
tempo_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$tempo_directory/tempo" migrate "$@"
