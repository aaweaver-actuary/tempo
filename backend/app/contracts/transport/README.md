# Owner-specific transport contracts

Current retained request/response models live in the named modules below.
They validate transport facts; routing, persistence, scheduling and lifecycle
belong to callers. The former `app.models` facade is removed.

| Owner | Contract module | Existing classes |
| --- | --- | --- |
| Product settings contract | [`settings.py`](settings.py) | `Settings` |
| Study queue/review/teaching contracts | [`study.py`](study.py) | `QueueAttemptFailureRequest`, `PreparedNextIdentity`, `ReviewRequest`, `TeachingStateRequest` |
| Authored line/paste contracts | [`authored_lines.py`](authored_lines.py) | `BranchRequest`, `AnalysisPastePreviewRequest`, `AnalysisPasteSelection`, `AnalysisPasteCommitRequest`, `RemoveBranchRequest` |
| Authored integrity/coverage contracts | [`authored_integrity.py`](authored_integrity.py) | `IntegrityResolutionRequest`, `CoverageMaiaMove`, `CoverageMaiaSubmission` |
| Content card definition/prefix split contracts | [`content.py`](content.py) | `CardRevisionRequest`, `PrefixSplitRequest`, `PrefixSplitCard`, `PrefixSplitResponse` |
| Authored repertoire settings/annotation contracts | [`authored_repertoire.py`](authored_repertoire.py) | `RepertoireSettingsRequest`, `RepertoireSettingsResponse`, `RepertoireRenameRequest`, `AnnotationArrow`, `AnnotationSquare`, `PositionAnnotationRequest` |
| Tactics contracts | [`tactics.py`](tactics.py) | `TacticCaptureRequest`, `TacticCaptureResponse`, `TacticAttemptRequest`, `TacticActivationRequest` |
| Authored PGN import response contract | [`authored_import.py`](authored_import.py) | `ImportResult` |

Presence of retained Coverage or exercise DTOs is not activation of a retired
worker or endpoint. Endgame generation, engine/defense analysis, Discovery,
Games provider/sync/findings and the old Games read DTO modules are absent.
The retained [Study contract](study.py) imports explicit
[opening evidence](../opening_evidence.py); authored exercise definitions live in
[study_exercises.py](../study_exercises.py), not an independent Learning runtime.
[ReviewConflict](../review_conflicts.py) retains HTTP transport coupling.

The historical 62-model/17-group extraction and 125-case receipt described the
old tree. They are not current test counts or runtime qualification. For current
checks use the [test selection policy](../../../../docs/testing.md).
