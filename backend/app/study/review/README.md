# Study review navigation

This directory currently retains only its [initializer](__init__.py).
The former `feedback_contracts.py`, miss-feedback protocols and receiving
projection assembly are retired; do not import them as current public contracts.

Current review commands enter through [review_commands.py](../../review_commands.py),
with [Study position learning](../position_learning) and the retained
[Learning review application library](../../../../services/learning/application/README.md).
[Concrete review storage](../../../../database/tempo_storage/study) owns persistence.
These pointers do not imply that all review paths have one newly unified API.
Preserve original effect/receipt/checkpoint atomicity and issued-attempt identity;
source removal is never permission to discard accepted work.
