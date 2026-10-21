"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import threat_models as _owned
dataclass = _owned.dataclass
Literal = _owned.Literal
Color = _owned.Color
LineOrigin = _owned.LineOrigin
GameSnapshot = _owned.GameSnapshot
SourceLine = _owned.SourceLine
ForkTarget = _owned.ForkTarget
ForkGeometry = _owned.ForkGeometry
ThreatSeed = _owned.ThreatSeed
KnightHop = _owned.KnightHop
PositionContext = _owned.PositionContext
ExerciseAnchor = _owned.ExerciseAnchor
ThreatPolicy = _owned.ThreatPolicy
