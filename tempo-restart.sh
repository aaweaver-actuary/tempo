#!/bin/sh
set -eu
tempo_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$tempo_directory/tempo" restart "$@"
