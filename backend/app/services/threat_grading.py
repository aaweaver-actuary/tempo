"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import threat_grading as _owned
dataclass = _owned.dataclass
Literal = _owned.Literal
ExerciseAnchor = _owned.ExerciseAnchor
ThreatPolicy = _owned.ThreatPolicy
ThreatSeed = _owned.ThreatSeed
AnalysisLine = _owned.AnalysisLine
AnalysisReport = _owned.AnalysisReport
AnalysisRequest = _owned.AnalysisRequest
_fork_consequence = _owned._fork_consequence
_learner_loss = _owned._learner_loss
_mate_outcome = _owned._mate_outcome
_replay_line = _owned._replay_line
GradeStatus = _owned.GradeStatus
DefenseExercise = _owned.DefenseExercise
MoveGrade = _owned.MoveGrade
_board_at_anchor = _owned._board_at_anchor
_compatible_root_report = _owned._compatible_root_report
_needed_request = _owned._needed_request
grade_defense_move = _owned.grade_defense_move
