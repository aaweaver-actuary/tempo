#!/usr/bin/env zsh

cd /Users/andy/tempo

export TEMPO_UPGRADE_EXPECTED_PROJECT=tempo
UPDATE_PLAN="$(sh scripts/upgrade-postgres-schema.sh --plan)"

# Pause and wait for user confirmation before proceeding with the upgrade
echo "The following schema update plan has been generated:"
echo "$UPDATE_PLAN"

echo "Do you want to proceed with the schema update? (yes/no)"
read USER_CONFIRMATION

if [[ "$USER_CONFIRMATION" == "yes" ]]; then
    echo "Proceeding with the schema update..."
    sh scripts/upgrade-postgres-schema.sh --apply
else
    echo "Schema update aborted by user."
fi



