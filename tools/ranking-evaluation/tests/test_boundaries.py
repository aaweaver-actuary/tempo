from dataclasses import FrozenInstanceError
import os
import sys
import pytest
from tempo_evidence_math import ProbabilityEvidence
from tempo_ranking_evaluation import run_corpus
from tempo_ranking_evaluation.resources import installed_corpus_directory


def test_installed_public_evaluator_and_corpus_have_no_service_source_dependency():
    import tempo_evidence_math
    import tempo_ranking_evaluation
    for package in (tempo_evidence_math,tempo_ranking_evaluation):
        assert 'site-packages' in package.__file__
    assert (installed_corpus_directory()/'manifest.json').is_file()
    assert len(list(installed_corpus_directory().glob('*.json')))==7
    assert not {'app','backend','sqlite3','psycopg','redis','fsrs','tempo_scheduling'} & set(sys.modules)


def test_evaluator_runtime_guard_rejects_deliberate_initialization():
    assert os.environ.get('TEMPO_RUNTIME_GUARD')=='1','Run through the owning qualifier'
    for operation in ("__import__('sqlite3')", "__import__('socket').create_connection(('127.0.0.1',1))", "__import__('subprocess').run([__import__('sys').executable,'-c','pass'])"):
        namespace={'__name__':'tempo_ranking_evaluation.deliberate_probe'}
        exec(compile('def probe():\n    '+operation,'<deliberate-probe>','exec'),namespace)
        with pytest.raises(AssertionError):namespace['probe']()
