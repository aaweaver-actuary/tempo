"""Reuse Learning's owning disposable fixture; this is an explicit assembly lane."""
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[5]
regular=ROOT/'services/learning/runtime/tests'
sys.path.insert(0,str(regular))
spec=importlib.util.spec_from_file_location('C_learning_owned_fixtures',regular/'conftest.py')
fixtures=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixtures;spec.loader.exec_module(fixtures)
case=fixtures.case
sys.path.insert(0,str(ROOT/'services/learning/runtime/integration/study_tests'))
