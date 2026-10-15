"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import scheduling_review_contracts as _owned
dataclass = _owned.dataclass
datetime = _owned.datetime
Protocol = _owned.Protocol
TypedDict = _owned.TypedDict
CardState = _owned.CardState
ReviewRecord = _owned.ReviewRecord
OpeningSeedFacts = _owned.OpeningSeedFacts
MaturityFacts = _owned.MaturityFacts
UpdatedCardState = _owned.UpdatedCardState
SchedulingReviewResult = _owned.SchedulingReviewResult
SchedulingReviewStore = _owned.SchedulingReviewStore
