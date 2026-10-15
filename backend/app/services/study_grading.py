"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import study_grading as _owned
ChoiceAnswer = _owned.ChoiceAnswer
ChoiceExercise = _owned.ChoiceExercise
ExplanationAnswer = _owned.ExplanationAnswer
ExplanationExercise = _owned.ExplanationExercise
KnightAnswer = _owned.KnightAnswer
KnightExercise = _owned.KnightExercise
MoveAnswer = _owned.MoveAnswer
MoveExercise = _owned.MoveExercise
SquareAnswer = _owned.SquareAnswer
SquareExercise = _owned.SquareExercise
GRADER_VERSION = _owned.GRADER_VERSION
_square = _owned._square
validate_exercise = _owned.validate_exercise
_knight_landings = _owned._knight_landings
_valid_knight_destination = _owned._valid_knight_destination
available_knight_routes = _owned.available_knight_routes
evaluate_answer = _owned.evaluate_answer
