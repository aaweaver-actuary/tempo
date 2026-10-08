# Choose one verification scope per invocation. `make full` is the release gate;
# running a smaller scope first is useful during development but repeats that
# scope when the required final full gate runs.
#
# Full order, with permissions checked before any test family:
#  0 Docker + loopback + checkout bind-mount preflight
#  1 unit                         7 lint
#  2 defense-engine smoke         8 typecheck
#  3 Python backend tests         9 WASM build
#  4 Rust format                 10 local frontend build
#  5 Rust lint                   11 PostgreSQL durability + regular browser matrix
#  6 Rust workspace tests        12 pinned visual + performance Playwright tests
# The local browser target is deliberately absent from full: Docker already
# runs the same regular Playwright specs. Visual/performance specs are disjoint.

.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help plan preflight slow-tests fast python backend rust integration ui browser visual perf full docker-durability docker-lifecycle legacy-sqlite unit-file python-file ui-file view rust-case

VERIFY_TARGETS := preflight fast python backend rust integration ui browser visual perf full docker-durability docker-lifecycle legacy-sqlite unit-file python-file ui-file view rust-case
SELECTED_VERIFY_TARGETS := $(filter $(VERIFY_TARGETS),$(MAKECMDGOALS))
ifneq ($(words $(SELECTED_VERIFY_TARGETS)),0)
ifneq ($(words $(SELECTED_VERIFY_TARGETS)),1)
$(error Choose one verification target per invocation to avoid duplicate test runs)
endif
endif

TIER ?= full
COUNT ?= 10
PYTHON ?= $(shell node --input-type=module -e 'import { resolvePython } from "./scripts/resolve-python.mjs"; console.log(resolvePython())')
export FILE VIEW

help:
	@printf '%s\n' 'Inspect: make plan [TIER=full|fast|python|backend|rust|integration|ui|browser]'
	@printf '%s\n' '         make slow-tests [TIER=full|fast] [COUNT=10] (reads last unit profile)'
	@printf '%s\n' 'Release/CI-equivalent: make full (run this one target, not fast + integration + full)'
	@printf '%s\n' 'Capability check: make preflight (full and browser scopes run it first automatically)'
	@printf '%s\n' 'Focused scopes: make fast | python | backend | rust | integration | ui | browser | visual | perf'
	@printf '%s\n' 'Browser scopes exclude recovery/backup checks; make full still requires them all.'
	@printf '%s\n' 'Docker recovery: make docker-durability (ordinary PostgreSQL durability without browser/lifecycle)'
	@printf '%s\n' 'Deployment rehearsal: make docker-lifecycle (complete CLI recovery/deployment lifecycle)'
	@printf '%s\n' 'Optional compatibility: make legacy-sqlite (full SQLite runtime/browser runner)'
	@printf '%s\n' 'Focused files: make unit-file FILE=tests/unit/example.test.ts'
	@printf '%s\n' '               make python-file FILE=backend/tests/test_services.py'
	@printf '%s\n' '               make ui-file FILE=games-board-context.spec.ts'
	@printf '%s\n' 'Focused titles: make view VIEW=Builder | make rust-case FILTER=card_identity'
	@printf '%s\n' 'view matches test titles; use ui-file for an exact browser spec.'

plan:
	node scripts/test-all.mjs --list "$(TIER)"

preflight:
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount

slow-tests:
	node scripts/report-slow-unit-files.mjs "$(TIER)" "$(COUNT)"

fast:
	node scripts/test-all.mjs fast

python:
	node scripts/test-all.mjs python

backend:
	node scripts/test-all.mjs backend

rust:
	node scripts/test-all.mjs rust

integration:
	node scripts/test-all.mjs integration

ui:
	node scripts/test-all.mjs ui

browser:
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount
	node scripts/test-postgres-docker.mjs --mode browser

visual:
	node scripts/check-test-capabilities.mjs --docker --workspace-mount
	npm run test:visual

perf:
	node scripts/check-test-capabilities.mjs --docker --workspace-mount
	npm run test:perf

full:
	node scripts/test-all.mjs full

docker-durability:
	node scripts/check-test-capabilities.mjs --docker --loopback
	node scripts/test-postgres-docker.mjs --mode durability

docker-lifecycle:
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount
	node scripts/test-postgres-docker.mjs --mode lifecycle

legacy-sqlite:
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount
	node scripts/test-docker.mjs

unit-file:
	@test -n "$(FILE)" || { echo 'Set FILE=tests/unit/<name>.test.ts'; exit 2; }
	npm run test:unit -- "$(FILE)"

python-file:
	@test -n "$(FILE)" || { echo 'Set FILE=backend/tests/test_<name>.py'; exit 2; }
	PYTHONPATH=backend "$(PYTHON)" -m pytest "$(FILE)" -q -o cache_dir=.pytest_cache --rootdir=.

ui-file:
	@test -n "$(FILE)" || { echo 'Set FILE=<name>.spec.ts from tests/browser'; exit 2; }
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount
	node scripts/run-focused-postgres-browser.mjs file

view:
	@test -n "$(VIEW)" || { echo 'Set VIEW to a browser test title pattern, for example VIEW=Builder'; exit 2; }
	node scripts/check-test-capabilities.mjs --docker --loopback --workspace-mount
	node scripts/run-focused-postgres-browser.mjs grep

rust-case:
	@test -n "$(FILTER)" || { echo 'Set FILTER to a Rust test name'; exit 2; }
	cargo test --workspace "$(FILTER)"
