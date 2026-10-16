# External C pilot

Consumes A's `tempo-analysis-inputs` version 1 serialized manifest/pages. Verifies
all hashes, counts, workspace, exact selected games, position history and original
comparison identity, then normalizes into C's public analysis-v1 input API.
No Content/Game Evidence implementation or foreign database is imported.
B's optional execution lives here, using only `tempo_stockfish_client` public API;
Intelligence reports do not require this tool or B to be installed.

Install the C and B client wheels, shared analysis-job/chess artifacts, then this
wheel. `tempo-analysis-pilot --config /absolute/private/pilot.json --evidence
/absolute/private/evidence` invokes installed C commands. Required config: integer
`schema_version:1`, `workspace_id`, `a_export_directory`, exact `game_ids`,
`intelligence_command` argv and `run_id`; optional `position_budget` (250),
`engine_profile`, `max_jobs` (1). Existing C runtime environment owns its database.
No credentials belong in config/argv; use C's private credential-file configuration.

`tempo-analysis-pilot work RUN --profile /absolute/profile.json --max-jobs 1`
executes bounded B work through C's public installed composition API. Profile has
`schema_version:1`, `command` argv, exact `engine_version`, and `memory_mb:512`.
The command must launch B's installed worker inside its qualified hard cgroup.
A configured profile is not evidence of actual execution or activation.

Pilot exports/evidence are private and remain outside Git. A synthetic export stays
synthetic; C3 is unverified until an explicitly scoped real export is supplied.


For actual artifact acceptance, set the four paths named in REGRESSIONS.md, then
run `python -I -m pytest integration/test_actual_consumers.py --import-mode=importlib`.
The installed producer fixture is not Andy's real repertoire or game history.
A's `positions[].opponent_decision` supplies known eligible encounter denominators;
older exports keep them unknown. Native reply scopes are consumed as exported.
Absent delivery inspection remains `unknown / delivery_not_exported`.

Source-removed qualification: `python qualify.py --c-artifacts ABSOLUTE_C_WHEEL_DIR
--b-client ABSOLUTE_B_CLIENT_WHEEL --requirements ABSOLUTE_LOCKED_REQUIREMENTS
ABSOLUTE_EVIDENCE`. Requirements come from the selected Intelligence lock; the B
client digest and all output artifact digests are retained in the report.
