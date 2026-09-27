"""Inventory Tempo's persistent-data call sites before a storage cutover.

The report is deliberately syntax based: dynamic SQL and helper functions that
receive a connection still need human review. It records their containing
function so that review has a complete starting list.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend" / "app"
FRONTEND_ROOTS = (ROOT / "app", ROOT / "static" / "src")
SQL_METHODS = {"execute", "executemany", "executescript"}
DATABASE_ENTRYPOINTS = {
    "connection",
    "read_connection",
    "foreground_connection",
    "background_connection",
    "submit_foreground_write",
    "submit_background_write",
}
SQLITE_ONLY_TOKENS = (
    "sqlite3",
    "PRAGMA",
    "sqlite_master",
    "INSERT OR IGNORE",
    "INSERT OR REPLACE",
    "BEGIN IMMEDIATE",
    "json_extract(",
    "json_each(",
)


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def route_details(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    routes = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if not isinstance(decorator.func.value, ast.Name):
            continue
        if decorator.func.value.id not in {"app", "router"}:
            continue
        if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
            continue
        path = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) else "<dynamic>"
        routes.append({"method": decorator.func.attr.upper(), "path": path})
    return routes


def inspect_python_file(path: Path) -> dict:
    source = path.read_text()
    syntax = ast.parse(source, filename=str(path))
    relative_path = str(path.relative_to(ROOT))
    access_sites: list[dict] = []
    routes: list[dict] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_names: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            function_name = ".".join((*self.function_names, node.name))
            for route in route_details(node):
                routes.append({"file": relative_path, "line": node.lineno, "function": function_name, **route})
            self.function_names.append(node.name)
            self.generic_visit(node)
            self.function_names.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = call_name(node.func)
            if name in DATABASE_ENTRYPOINTS or name in SQL_METHODS:
                access_sites.append({
                    "file": relative_path,
                    "line": node.lineno,
                    "function": ".".join(self.function_names) or "<module>",
                    "operation": name,
                })
            self.generic_visit(node)

    Visitor().visit(syntax)
    sqlite_only = [
        {"file": relative_path, "line": line_number, "tokens": matching_tokens}
        for line_number, line in enumerate(source.splitlines(), 1)
        if (matching_tokens := [token for token in SQLITE_ONLY_TOKENS if token in line])
    ]
    return {"routes": routes, "access_sites": access_sites, "sqlite_only": sqlite_only}


def inspect_sqlite_schema(database_path: Path) -> list[dict]:
    database = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        tables = []
        for table_name, create_sql in database.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted_table_name = '"' + table_name.replace('"', '""') + '"'
            tables.append({
                "name": table_name,
                "create_sql": create_sql,
                "columns": [
                    {"name": row[1], "type": row[2], "not_null": bool(row[3]), "primary_key": row[5]}
                    for row in database.execute(f"PRAGMA table_info({quoted_table_name})")
                ],
                "foreign_keys": [
                    {"target_table": row[2], "source_column": row[3], "target_column": row[4], "on_delete": row[6]}
                    for row in database.execute(f"PRAGMA foreign_key_list({quoted_table_name})")
                ],
                "indexes": [
                    row[1] for row in database.execute(f"PRAGMA index_list({quoted_table_name})")
                ],
            })
        return tables
    finally:
        database.close()


def build_inventory(sqlite_path: Path | None = None) -> dict:
    backend_files = sorted(BACKEND_ROOT.rglob("*.py"))
    inspected = [inspect_python_file(path) for path in backend_files]
    frontend_callers = []
    for frontend_root in FRONTEND_ROOTS:
        for path in sorted(frontend_root.rglob("*.ts*")):
            source = path.read_text()
            if re.search(r"(?:API_URL|/api/|\bfetch\s*\(|\bbackgroundFetch\s*\()", source):
                frontend_callers.append(str(path.relative_to(ROOT)))
    inventory = {
        "routes": [route for file_report in inspected for route in file_report["routes"]],
        "backend_access_sites": [site for file_report in inspected for site in file_report["access_sites"]],
        "sqlite_only_sites": [site for file_report in inspected for site in file_report["sqlite_only"]],
        "frontend_callers": frontend_callers,
    }
    if sqlite_path is not None:
        inventory["sqlite_tables"] = inspect_sqlite_schema(sqlite_path)
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    parser.add_argument("--sqlite-path", type=Path, help="Read table schema from a SQLite snapshot")
    options = parser.parse_args()
    payload = json.dumps(build_inventory(options.sqlite_path), indent=2, sort_keys=True) + "\n"
    if options.output:
        options.output.write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
