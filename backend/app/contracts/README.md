# Explicit backend contracts

Use named contract modules, not a wildcard public barrel or composition import.
The [initializer](__init__.py) performs no runtime registration. Current owners:

- [Transport requests](transport/README.md).
- [Opening evidence](opening_evidence.py) and [graph facts](opening_graph.py).
- [Authored Studies](studies.py) and [canonical exercise contracts](study_exercises.py).
- [Queue issuance](queue_issuance.py) and [Tactics capture](tactic_capture.py).
- [Command identity](command_identity.py), [command registry facts](command_registry.py),
  [review identity](review_command_identity.py),
  [analysis-paste identity](analysis_paste_identity.py) and
  [retained discovery identity](discovery_command_identity.py).
- [Review conflicts](review_conflicts.py) and [runtime timing](runtime_timing.py).
- [Retained product values](product_v02).

Some contracts contain pure validation helpers; framework dependencies such as
Pydantic and the existing HTTP-aware review conflict remain explicit. Retained
command identity does not imply an active endpoint or producer. The independent
Learning runtime and its former defense/exercise alias authority are gone;
`studies.py` now aliases the local `study_exercises.py` definitions. The removed
next-opponent, move-probability, prefix diagnostic/evaluation/transition and
defense modules are not current interfaces. These source facts are not a claim
that all contracts are framework-free or all consumers are isolated.
