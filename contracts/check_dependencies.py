"""Contract-only AST boundary proof; not a repository-wide dependency planner."""

import ast
from pathlib import Path
import sys

FORBIDDEN_LOADERS = {
    "__import__",
    "eval",
    "exec",
    "compile",
    "import_module",
    "load_module",
    "exec_module",
    "spec_from_file_location",
}


def check_package(source_root: Path):
    errors = []
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.level:
                    containing = source_path.parent
                    for _ in range(node.level - 1):
                        containing = containing.parent
                    if source_root.resolve() not in (
                        containing.resolve(),
                        *containing.resolve().parents,
                    ):
                        errors.append(
                            f"{source_path}:{node.lineno}: relative source escape"
                        )
                    if node.module:
                        target = containing.joinpath(*node.module.split("."))
                        if (
                            not target.with_suffix(".py").exists()
                            and not (target / "__init__.py").exists()
                        ):
                            errors.append(
                                f"{source_path}:{node.lineno}: absent private module {node.module}"
                            )
                    else:
                        for alias in node.names:
                            if alias.name == "*" or not (
                                (containing / alias.name).with_suffix(".py").exists()
                                or (containing / alias.name / "__init__.py").exists()
                            ):
                                errors.append(
                                    f"{source_path}:{node.lineno}: unresolved relative import {alias.name}"
                                )
                    continue
                imports = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                )
                for name in imports:
                    if name is None or name.split(".")[
                        0
                    ] not in sys.stdlib_module_names | {"pydantic"}:
                        errors.append(
                            f"{source_path}:{node.lineno}: undeclared/cross-component import {name}"
                        )
            if isinstance(node, ast.Call):
                function_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                if function_name in FORBIDDEN_LOADERS:
                    errors.append(
                        f"{source_path}:{node.lineno}: dynamic loader {function_name}"
                    )
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
                and node.attr == "path"
            ):
                errors.append(
                    f"{source_path}:{node.lineno}: ambient search path access"
                )
    if not list(source_root.rglob("*.py")):
        errors.append(f"{source_root}: zero source files")
    return errors


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    source_roots = [path.parent / "src" for path in root.glob("*/pyproject.toml")]
    if len(source_roots) != 5:
        raise SystemExit("expected exactly five contract package inputs")
    errors = [error for directory in source_roots for error in check_package(directory)]
    if errors:
        raise SystemExit("\n".join(errors))
    print("Five contract packages: declared imports only; no dynamic source loading")
