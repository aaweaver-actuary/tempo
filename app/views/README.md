# Workspace views

Views compose the user-facing workspaces: Train, Builder, Repertoire, Games,
Progress, Statistics, Tactics, Endgames, Settings, and related dialogs.

Several views are currently large because they combine rendering, transport,
validation, and workflow orchestration. That is a staged extraction target, not
a reason to introduce speculative layers. New multi-step workflows should live
in a hook or feature module with a narrow view-facing contract.
