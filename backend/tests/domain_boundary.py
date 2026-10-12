"""Test-owned dependency rules; this module never imports application code."""
from __future__ import annotations

import ast
from datetime import date
from hashlib import sha256
import importlib.util
from pathlib import Path


FORBIDDEN_ROOTS = frozenset({
    "sqlite3", "_sqlite3", "psycopg", "psycopg2", "psycopg_pool", "asyncpg",
    "sqlalchemy", "redis", "celery", "fastapi", "starlette", "uvicorn",
    "httpx", "requests", "aiohttp", "socket", "urllib", "http", "ftplib",
    "subprocess", "multiprocessing", "concurrent", "os", "pathlib", "io",
    "shutil", "tempfile", "glob", "random", "time",
})
REGRESSION_NAMES = frozenset(
    node.name for node in ast.walk(ast.parse(Path(__file__).with_name("test_domain_boundaries.py").read_text()))
    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
)


class BoundaryViolation(ValueError):
    pass


def module_file(source_root: Path, module: str) -> Path | None:
    base = source_root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def dotted_name(node: ast.AST, bindings: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return dotted_name(node.value, bindings) + "." + node.attr
    return "<unresolved>"


def validate_exceptions(metadata: dict, observed: set[tuple[str, str, str]], *, today: date) -> None:
    identities = set()
    for exception in metadata["exceptions"]:
        required = ("module", "symbol", "operation", "owner", "reason", "regression", "review_milestone", "review_by", "expression_sha256")
        if any(not isinstance(exception.get(field), str) or not exception[field].strip() for field in required):
            raise BoundaryViolation("Exception must be explicit, owned and bounded")
        identity = tuple(exception[field] for field in ("module", "symbol", "operation"))
        if any("*" in value for value in identity) or identity in identities:
            raise BoundaryViolation("Wildcard or duplicate exception")
        identities.add(identity)
        if exception["module"] not in metadata["modules"]:
            raise BoundaryViolation("Exception outside certified modules")
        if exception["regression"] not in REGRESSION_NAMES:
            raise BoundaryViolation("Exception needs a named regression")
        deadline = date.fromisoformat(exception["review_by"])
        if deadline < today or deadline > date(2026, 11, 12):
            raise BoundaryViolation("Expired or extended exception review deadline")
    if identities != observed:
        raise BoundaryViolation(f"Unused or unreviewed exceptions: {identities ^ observed}")


def dependency_graph(source_root: Path, metadata: dict, *, today: date | None = None) -> dict[str, list[str]]:
    """Walk all local import statements, including unreachable/lazy branches.

    Stdlib value modules and reviewed third-party entrypoints are explicit leaves.
    Their runtime closure is independently tested in fresh, guarded processes.
    """
    certified_modules = set(metadata["modules"])
    package_modules = {"app", "app.services"}
    allowed_external = set(metadata["stdlib"]) | set(metadata["third_party"])
    graph: dict[str, list[str]] = {}
    observed_exceptions: set[tuple[str, str, str]] = set()
    exceptions = {tuple(item.get(field) for field in ("module", "symbol", "operation"))
                  for item in metadata["exceptions"]}

    def fail(path: tuple[str, ...], detail: str):
        raise BoundaryViolation(" -> ".join(path) + ": " + detail)

    def visit(module: str, path: tuple[str, ...]):
        if module in graph:
            return
        if module.split(".")[0] in FORBIDDEN_ROOTS:
            fail(path, "forbidden dependency")
        source_file = module_file(source_root, module)
        if source_file is None:
            if module not in allowed_external:
                fail(path, "unreviewed external dependency")
            graph[module] = []
            return
        if module not in certified_modules | package_modules:
            fail(path, "uncertified local dependency")
        graph[module] = []
        package = module if source_file.name == "__init__.py" else module.rpartition(".")[0]
        tree = ast.parse(source_file.read_text(), filename=str(source_file))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        bindings: dict[str, str] = {}
        imports: list[tuple[str, list[str]]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bindings[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                    imports.append((alias.name, []))
            elif isinstance(node, ast.ImportFrom):
                imported_module = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                if not imported_module:
                    fail(path, "unresolved relative import")
                imports.append((imported_module, [alias.name for alias in node.names]))
                for alias in node.names:
                    bindings[alias.asname or alias.name] = imported_module + "." + alias.name
        # Resolve simple aliases conservatively; this also catches loader = __import__.
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = dotted_name(node.value, bindings)

        def edge(dependency: str):
            if dependency not in graph[module]:
                graph[module].append(dependency)
            parts = dependency.split(".")
            for depth in range(1, len(parts)):
                parent = ".".join(parts[:depth])
                if module_file(source_root, parent):
                    visit(parent, (*path, parent))
            visit(dependency, (*path, dependency))

        for dependency, names in imports:
            edge(dependency)
            if dependency in metadata["third_party"]:
                allowed_symbols = metadata["third_party"][dependency]["symbols"]
                if names and any(name not in allowed_symbols for name in names):
                    fail((*path, dependency), "unreviewed third-party symbol or wildcard")
            for name in names:
                child = dependency + "." + name
                dependency_file = module_file(source_root, dependency)
                if module_file(source_root, child) or (dependency_file and dependency_file.name == "__init__.py"):
                    edge(child)
                elif name == "*":
                    fail(path, "wildcard import cannot certify exported capabilities")

        def calls(node: ast.AST, symbol: str = "<module>"):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = node.name if symbol == "<module>" else symbol + "." + node.name
            if isinstance(node, (ast.Name, ast.Attribute)):
                resolved_name = dotted_name(node, bindings)
                reference = resolved_name.split(".")
                if len(reference) > 1 and reference[0] in metadata["third_party"]:
                    if reference[1] not in metadata["third_party"][reference[0]]["symbols"]:
                        fail(path, f"unreviewed third-party capability {'.'.join(reference)}")
                if resolved_name in {"__import__", "builtins.__import__", "importlib.import_module"}:
                    parent = parents.get(node)
                    if not (isinstance(parent, ast.Call) and parent.func is node) and not isinstance(parent, ast.Assign):
                        fail(path, "unreviewed import callable escape")

            def exception_match(operation):
                identity = (module, symbol, operation)
                if identity not in exceptions:
                    fail(path, f"unreviewed capability {operation} in {symbol}")
                if identity in observed_exceptions:
                    fail(path, f"exception widened to multiple calls: {identity}")
                reviewed = next(item for item in metadata["exceptions"]
                                if tuple(item[field] for field in ("module", "symbol", "operation")) == identity)
                expression_digest = sha256(ast.unparse(node).encode()).hexdigest()
                if expression_digest != reviewed["expression_sha256"]:
                    fail(path, f"exception expression changed: {identity}")
                observed_exceptions.add(identity)

            if isinstance(node, ast.Call):
                operation = dotted_name(node.func, bindings)
                if operation in {"__import__", "builtins.__import__", "importlib.import_module"}:
                    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                        fail(path, f"unresolved dynamic import at line {node.lineno}")
                    dependency = node.args[0].value
                    if dependency.startswith("."):
                        if len(node.args) > 1:
                            dynamic_package = node.args[1]
                        else:
                            dynamic_package = next((keyword.value for keyword in node.keywords if keyword.arg == "package"), None)
                        if not isinstance(dynamic_package, ast.Constant) or not isinstance(dynamic_package.value, str):
                            fail(path, "unresolved dynamic relative import")
                        dependency = importlib.util.resolve_name(dependency, dynamic_package.value)
                    edge(dependency)
                elif operation in {"eval", "exec", "compile", "getattr", "setattr", "globals", "locals", "vars"}:
                    exception_match(operation)
                elif operation in {"open", "builtins.open", "input", "breakpoint"} or operation.endswith((".read_text", ".write_text", ".read_bytes", ".write_bytes", ".open")):
                    fail(path, f"I/O capability {operation} at line {node.lineno}")
                elif operation.rsplit(".", 1)[-1] in {"execute", "executemany", "cursor", "commit", "rollback", "connect", "connect_ex", "request", "send", "recv", "getaddrinfo"}:
                    fail(path, f"service capability {operation} at line {node.lineno}")
                elif operation in {"datetime.datetime.now", "datetime.datetime.today", "datetime.datetime.utcnow", "datetime.date.today"} or operation == "fsrs.Card":
                    if operation != "fsrs.Card" or not {"card_id", "due"}.issubset({keyword.arg for keyword in node.keywords}):
                        exception_match(operation)
            for child in ast.iter_child_nodes(node):
                calls(child, symbol)

        calls(tree)
        graph[module].sort()

    for root in metadata["modules"]:
        for package in ("app", "app.services"):
            if module_file(source_root, package):
                visit(package, (root, package))
        visit(root, (root,))
    validate_exceptions(metadata, observed_exceptions, today=today or date.today())
    return dict(sorted(graph.items()))
