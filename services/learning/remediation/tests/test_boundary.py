from importlib import resources
from pathlib import Path
import ast
import tempo_learning_remediation


def test_installed_public_package_and_packaged_migration_have_no_runtime_source_dependency():
    installed=Path(tempo_learning_remediation.__file__).resolve()
    assert "site-packages" in installed.parts
    migration=resources.files(tempo_learning_remediation).joinpath("migrations/001_remediation.sql").read_text()
    assert "CREATE TABLE remediation_deliveries" in migration
    for source in installed.parent.glob("*.py"):
        imports=[node.module for node in ast.walk(ast.parse(source.read_text())) if isinstance(node,ast.ImportFrom) and node.module]
        assert not any(name.startswith(("app.","backend.","services.","tempo_scheduling")) for name in imports)


def test_runtime_has_no_connection_commit_rollback_or_lifetime_owner():
    source=Path(tempo_learning_remediation.__file__).resolve().parent
    for path in source.glob("*.py"):
        tree=ast.parse(path.read_text())
        calls=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
        assert not set(calls)&{"connect","commit","rollback","close"}
