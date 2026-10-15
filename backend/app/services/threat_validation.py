"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import threat_validation as _owned
asdict = _owned.asdict
dataclass = _owned.dataclass
Literal = _owned.Literal
defensive_fork_geometry = _owned.defensive_fork_geometry
ExerciseAnchor = _owned.ExerciseAnchor
ThreatPolicy = _owned.ThreatPolicy
ThreatSeed = _owned.ThreatSeed
EngineScore = _owned.EngineScore
AnalysisRequest = _owned.AnalysisRequest
AnalysisLine = _owned.AnalysisLine
AnalysisReport = _owned.AnalysisReport
ValidationState = _owned.ValidationState
ValidationPlan = _owned.ValidationPlan
ValidationResult = _owned.ValidationResult
RecognitionPreview = _owned.RecognitionPreview
make_validation_plan = _owned.make_validation_plan
_anchor_board = _owned._anchor_board
_replay_line = _owned._replay_line
recognition_preview = _owned.recognition_preview
_learner_loss = _owned._learner_loss
_mate_outcome = _owned._mate_outcome
_fork_consequence = _owned._fork_consequence
_capturable_control_line = _owned._capturable_control_line
_validated_control = _owned._validated_control
validate_threat_anchor = _owned.validate_threat_anchor
