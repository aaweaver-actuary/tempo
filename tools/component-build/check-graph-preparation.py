"""Static imports for two graph/fresh-start helpers; never execute either one."""
import ast
import json
import sys

SOURCES = {"database/tooling/opening_graph_transfer.py",
           "database/tooling/opening_graph_fresh_learning.py"}
# File/argument handling belongs to these offline tools; concrete database,
# network, process and service clients must remain injected capabilities.
ALLOWED = {"argparse", "collections", "copy", "dataclasses", "datetime", "hashlib",
           "json", "math", "pathlib", "re", "sys", "types", "typing", "zoneinfo"}
PURE_SUBMODULES = {"urllib.parse"}
LOADERS = {"__import__", "eval", "exec", "compile"}
LOADER_METHODS = {"import_module", "load_module", "exec_module", "spec_from_file_location"}


def check_source(path, source):
    if path not in SOURCES:
        raise ValueError("unknown graph preparation source")
    tree = ast.parse(source, filename=path)
    sys_aliases = {alias.asname or "sys" for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names if alias.name == "sys"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = ([node.module] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names])
            if (isinstance(node, ast.ImportFrom) and node.level) or any(
                not module or (module.split(".")[0] not in ALLOWED and module not in PURE_SUBMODULES)
                for module in modules
            ):
                raise ValueError(f"{path}:{node.lineno}: undeclared graph preparation import")
            if isinstance(node, ast.ImportFrom) and node.module == "sys" and any(alias.name == "path" for alias in node.names):
                raise ValueError(f"{path}:{node.lineno}: ambient import path")
        if (isinstance(node, ast.Name) and node.id in LOADERS) or (
            isinstance(node, ast.Attribute) and node.attr in LOADER_METHODS
        ):
            raise ValueError(f"{path}:{node.lineno}: dynamic source loader")
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in sys_aliases and node.attr == "path"):
            raise ValueError(f"{path}:{node.lineno}: ambient import path")


if __name__ == "__main__":
    try:
        for path, source in json.load(sys.stdin).items():
            check_source(path, source)
        print(json.dumps({"graph_preparation_imports": "valid"}))
    except (ValueError, SyntaxError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
