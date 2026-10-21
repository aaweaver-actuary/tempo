"""Legacy authored Study keeps one contract identity with its owning grader."""
import json
from pathlib import Path

import pytest

from app.contracts import studies as legacy
from app.services.study_portable import validate_bundle
from tempo_learning_runtime import study_contracts as owned


EXECUTABLE_CONTRACTS = (
    'ExerciseText', 'MoveExercise', 'SquareExercise', 'KnightExercise', 'ChoiceOption',
    'ChoiceExercise', 'ExplanationExercise', 'ExerciseSpecification', 'MoveAnswer',
    'SquareAnswer', 'KnightAnswer', 'ChoiceAnswer', 'ExplanationAnswer', 'ExerciseAnswer',
)
REQUEST_ENVELOPES = (
    'StrictModel', 'StudyCreate', 'ChapterCreate', 'StudyImportPreviewRequest',
    'StudyImportCommitRequest', 'ExerciseCreate', 'ExerciseRevisionRequest',
    'StudyAttemptRequest', 'StudySelfAssessmentRequest', 'StudyLinkCreate', 'StudyBundleImportRequest',
)
CORPORA = sorted((Path(__file__).resolve().parents[2] / 'public/data/studies').glob(
    'stalemate-swindles-*.tempo-study.json'))


def test_legacy_study_contracts_share_learning_grader_model_identity():
    assert CORPORA, 'The real checked-in portable corpora must remain selected'
    mismatches = [name for name in EXECUTABLE_CONTRACTS if getattr(legacy, name) is not getattr(owned, name)]
    assert not mismatches, f'Legacy consumers construct contracts the Learning grader cannot recognize: {mismatches}'
    assert all(getattr(legacy, name).__module__ == 'app.contracts.studies' for name in REQUEST_ENVELOPES), \
        'Legacy endpoint envelopes retain their existing owner and fields'


@pytest.mark.parametrize('corpus', CORPORA, ids=lambda path: path.name)
def test_checked_in_stalemate_corpus_validates_with_legacy_learning_contract_boundary(corpus):
    bundle = json.loads(corpus.read_text())
    tables = validate_bundle(bundle)
    assert len(tables['study_exercises']) == len(bundle['tables']['study_exercises']) > 0
    assert tables['study_exercise_revisions'] == bundle['tables']['study_exercise_revisions']
