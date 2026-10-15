"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import opening_decision_evidence as _owned
datetime = _owned.datetime
timedelta = _owned.timedelta
timezone = _owned.timezone
ZoneInfo = _owned.ZoneInfo
OpeningDecisionManifest = _owned.OpeningDecisionManifest
OpeningEvidenceCheckpoint = _owned.OpeningEvidenceCheckpoint
POLICY_VERSION = _owned.POLICY_VERSION
presentation_occurrences = _owned.presentation_occurrences
stable_key = _owned.stable_key
EvidenceConflict = _owned.EvidenceConflict
evidence_time = _owned.evidence_time
decision_manifest = _owned.decision_manifest
validate_checkpoint = _owned.validate_checkpoint
reduce_observations = _owned.reduce_observations
