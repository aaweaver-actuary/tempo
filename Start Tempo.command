#!/bin/sh
set -eu
tempo_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${TEMPO_CLI_CONFIG:-$HOME/.config/tempo/config.json}" ]; then
  "$tempo_directory/tempo" install || exit 1
fi
if ! "$tempo_directory/tempo" start "$@"; then
  echo "Tempo did not start. The error above identifies the next action."
  printf "Press Return to close. "
  read -r tempo_response
  exit 1
fi
