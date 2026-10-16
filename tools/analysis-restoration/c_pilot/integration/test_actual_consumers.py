"""Actual serialized public artifact lane; explicit paths required, no fallback."""
import importlib.util
from pathlib import Path


def test_C_actual_AB_consumers():
    path=Path(__file__).with_name('actual_consumers.py')
    spec=importlib.util.spec_from_file_location('C_actual_public_consumers',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.main([])
