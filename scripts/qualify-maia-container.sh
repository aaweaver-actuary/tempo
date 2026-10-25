#!/bin/sh
set -eu
node --input-type=module -e 'import { writeFileSync } from "node:fs"; if (process.version !== "v" + process.env.EXPECTED_NODE || process.platform !== "linux" || process.arch !== "arm64") throw Error("Unexpected runtime"); writeFileSync("/evidence/runtime.json", JSON.stringify({node:process.version,platform:process.platform,arch:process.arch}) + "\n")'
npm --version > /evidence/npm-version.txt
# Install only this package, including its locked TypeScript test dependency.
npm ci --ignore-scripts --workspaces=false > /evidence/dependencies.log 2>&1
touch /evidence/dependencies-ready
# The host releases this barrier only after inspecting network disconnection.
while [ ! -f /evidence/network-disconnected ]; do sleep 1; done
export npm_config_offline=true
node test.mjs pure > /evidence/pure.json 2> /evidence/pure.stderr
node test.mjs model > /evidence/model.json 2> /evidence/model.stderr
node qualify.mjs restricted --report /evidence/installed.json --artifacts /evidence/artifacts > /evidence/qualifier.stdout 2> /evidence/qualifier.stderr
