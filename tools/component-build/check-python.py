"""AST boundary validation: includes conditional/function-local imports, without importing code."""
import ast
import json
import re
import tomllib
from pathlib import Path
import sys


def check_python(root, dependencies, exports, artifact=None):
    root = Path(root).resolve()
    sources = root / "src"
    allowed_external = set(dependencies)
    standard_library = set(sys.stdlib_module_names)
    package_directories = [path for path in sources.iterdir() if path.is_dir()]
    if len(package_directories) != 1:
        raise ValueError("Expected one owned import root")
    package = package_directories[0]
    problems = []
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    if artifact and any(metadata["project"][key] != artifact[key] for key in ("name", "version")):
        problems.append("Python artifact identity differs from manifest")
    declared_dependencies = {re.split(r"[<>=!~;\[]", requirement)[0].replace("-", "_") for requirement in metadata["project"].get("dependencies", [])}
    if declared_dependencies != allowed_external:
        problems.append("Dependencies differ from manifest")
    for source in sorted(package.rglob("*.py")):
        if source.is_symlink() or not source.resolve().is_relative_to(package):
            problems.append(f"{source}: source escape")
            continue
        tree = ast.parse(source.read_text())
        module_parts = source.relative_to(sources).with_suffix("").parts
        current_package = module_parts[:-1]
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = current_package[:len(current_package) - node.level + 1]
                    if not parent:
                        problems.append(f"{source}:{node.lineno}: relative source escape")
                        continue
                    imported = [".".join((*parent, *(node.module or "").split("."))).rstrip(".")]
                else:
                    imported = [node.module or ""]
            if isinstance(node, ast.Name) and node.id in {"__import__", "eval", "exec"}:
                problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in {"import_module", "exec_module", "load_module"}:
                problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {node.attr}")
            for imported_module in imported:
                import_root = imported_module.split(".")[0]
                if import_root in {"importlib", "runpy"}:
                    problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {imported_module}")
                if import_root == package.name:
                    module_path = sources.joinpath(*imported_module.split("."))
                    if not module_path.with_suffix(".py").is_file() and not (module_path / "__init__.py").is_file():
                        problems.append(f"{source}:{node.lineno}: unavailable local module {imported_module}")
                elif import_root not in standard_library and import_root not in allowed_external:
                    problems.append(f"{source}:{node.lineno}: undeclared dependency {imported_module}")
    public_tree = ast.parse((package / "__init__.py").read_text())
    declared_exports = next((ast.literal_eval(node.value) for node in public_tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)), None)
    if declared_exports != exports:
        problems.append("Python public exports differ from manifest")
    if problems:
        raise ValueError("\n".join(problems))
    return {"files": len(list(package.rglob("*.py"))), "exports": declared_exports, "package": package.name}


def check_consumer(source, package_name, exports):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(package_name + "."):
                    raise ValueError("Private Python implementation import: " + alias.name)
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == package_name or node.module.startswith(package_name + ".")):
            if node.module != package_name or any(alias.name not in exports for alias in node.names):
                raise ValueError("Private/unavailable Python export: " + node.module)

if __name__ == "__main__":
    try:
        if sys.argv[1] == "consumers":
            for source_path in Path(sys.argv[2]).rglob("*.py"):
                check_consumer(source_path.read_text(), sys.argv[3], json.loads(sys.argv[4]))
            print(json.dumps({"consumers": "valid"}))
        elif sys.argv[1] == "consumer":
            check_consumer(Path(sys.argv[2]).read_text(), sys.argv[3], json.loads(sys.argv[4]))
            print(json.dumps({"consumer": "valid"}))
        else:
            print(json.dumps(check_python(sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3]), json.loads(sys.argv[4]) if len(sys.argv) > 4 else None)))
    except (ValueError, SyntaxError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
