# GitHub workflows

Workflow files define CI and the limited GitHub Pages practice-demo publishing
path. Keep the published runtime clearly separate from Docker Tempo and keep
the quality runner aligned with the baseline environment.

`pages.yml` creates an explicit immutable plan, calls `verify-layer.yml` for
independent layers and aggregates required results into stable `quality`.
Complete verification runs nightly at 07:00 UTC and before practice-demo
publishing; schedules and verification-only manual runs cannot deploy.
The source/spec inventory, quarantine rules, baseline and owner settings
recommendations are in `docs/testing.md`. Repository settings are unchanged.
