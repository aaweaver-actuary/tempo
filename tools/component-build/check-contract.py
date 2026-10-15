"""Consume the contract catalog and its existing AST checker, not a second model catalog."""

import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib


def check_contract(root, component, catalog_path):
    root = Path(root).resolve()
    catalog_path = Path(catalog_path).resolve()
    entries = json.loads(catalog_path.read_text())["contracts"]
    entry = next(
        (item for item in entries if item["root"] == Path(component["root"]).name), None
    )
    if entry is None:
        raise ValueError("Contract root is absent from catalog")
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    project = metadata["project"]
    expected = (entry["distribution"], entry["artifact_version"])
    if (project["name"], project["version"]) != expected or (
        component["artifact"]["name"],
        component["artifact"]["version"],
    ) != expected:
        raise ValueError(
            "Contract artifact identity differs from catalog/package metadata"
        )
    requirements = project.get("dependencies", [])
    dependency_names = [re.split(r"[<>=!~;\[]", value)[0] for value in requirements]
    if requirements != entry["runtime_dependencies"] or sorted(
        dependency_names
    ) != sorted(component["dependencies"]):
        raise ValueError("Contract dependencies differ from catalog/manifest")
    if project["requires-python"] != component["toolchain"]["python"]:
        raise ValueError("Contract Python metadata differs from manifest")
    for namespace in [
        entry["public_namespace"],
        *[value for value in component["publicExports"] if "/" not in value],
    ]:
        module = root / "src" / Path(*namespace.split("."))
        if (
            not module.with_suffix(".py").is_file()
            and not (module / "__init__.py").is_file()
        ):
            raise ValueError("Unavailable public namespace/module: " + namespace)
    if entry["public_namespace"] not in component["publicExports"]:
        raise ValueError("Manifest omits catalog public namespace")
    package_name = entry["public_namespace"].split(".")[0]
    targets = metadata["tool"]["hatch"]["build"]["targets"]
    if targets["wheel"].get("force-include", {}).get(
        "schemas"
    ) != package_name + "/schemas" or not {"src", "schemas"} <= set(
        targets["sdist"].get("include", [])
    ):
        raise ValueError("Artifact metadata omits public schema/source exports")
    for source in (root / "src").rglob("*"):
        if source.is_symlink() or not source.resolve().is_relative_to(root / "src"):
            raise ValueError("Contract source symlink/escape: " + str(source))
    schema_exports = entry["schema_exports"]
    if not schema_exports or any(
        not (root / name).is_file() for name in schema_exports
    ):
        raise ValueError("Unavailable catalog schema export")
    for pattern in [value for value in component["publicExports"] if "/" in value]:
        matches = list(root.glob(pattern))
        if not matches or any(
            not path.is_file() or not path.resolve().is_relative_to(root)
            for path in matches
        ):
            raise ValueError("Unavailable/escaping schema export: " + pattern)
    public_source = (
        root / "src" / Path(*entry["public_namespace"].split(".")).with_suffix(".py")
    )
    ast.parse(public_source.read_text())
    specification = importlib.util.spec_from_file_location(
        "contract_boundary", catalog_path.parent / "check_dependencies.py"
    )
    checker = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(checker)
    errors = checker.check_package(root / "src")
    if errors:
        raise ValueError("\n".join(errors))
    return {
        "exports": component["publicExports"],
        "schemas": len(schema_exports),
        "owner": entry["authoritative_owner"],
        "wireVersion": entry["wire_version"],
    }


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                check_contract(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3])
            )
        )
    except (ValueError, OSError, SyntaxError, KeyError) as error:
        raise SystemExit(str(error))
