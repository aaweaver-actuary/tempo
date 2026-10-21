import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPECIFICATION = importlib.util.spec_from_file_location(
    "contract_boundary", ROOT / "check_dependencies.py"
)
BOUNDARY = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(BOUNDARY)


@pytest.mark.parametrize(
    "code",
    [
        "import backend.app.main",
        "def lazy():\n    import services.learning.internal",
        "if False:\n    import celery",
        "from numpy import array",
        "import importlib\nimportlib.import_module('backend.app.tasks')",
        "module = __import__('tempo_legacy_database.connections')",
        "import sys\nsys.path.append('/repository')",
        "from ...backend import models",
        "from .unavailable import private",
        "from . import unavailable",
    ],
)
def test_undeclared_direct_lazy_conditional_and_dynamic_dependencies_fail(
    tmp_path, code
):
    source = tmp_path / "src" / "example"
    source.mkdir(parents=True)
    (source / "v1.py").write_text(code)
    assert BOUNDARY.check_package(tmp_path / "src"), code


def test_contract_source_import_boundaries_hold():
    for source in (path.parent / "src" for path in ROOT.glob("*/pyproject.toml")):
        assert BOUNDARY.check_package(source) == []


def test_empty_dependency_selection_is_not_success(tmp_path):
    assert BOUNDARY.check_package(tmp_path)


def test_installed_imports_do_not_load_application_registry_or_model_runtimes(tmp_path):
    script = """
import sys
from tempo_learning_contracts import v1 as learning
from tempo_content_publication_contracts import v1 as content
from tempo_game_evidence_contracts import v1 as evidence
from tempo_intelligence_contracts import v1 as intelligence
from tempo_analysis_jobs_contracts import v1 as jobs
for forbidden in ('backend', 'app', 'services', 'celery', 'psycopg', 'torch', 'numpy', 'onnxruntime', 'fastapi', 'chess'):
    assert not any(name == forbidden or name.startswith(forbidden+'.') for name in sys.modules), forbidden
assert len(learning.PUBLIC_MODELS) == 9
assert jobs.StockfishJob is not jobs.MaiaJob
print('isolated public imports passed')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
