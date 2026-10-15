"""Verify installed public namespaces and packaged schema resources against their catalog."""

import importlib
import importlib.metadata
import json
from pathlib import Path
import sys


def verify(component, catalog, source):
    entry = next(
        item
        for item in catalog["contracts"]
        if item["distribution"] == component["artifact"]["name"]
    )
    assert (
        importlib.metadata.version(entry["distribution"]) == entry["artifact_version"]
    )
    for namespace in [
        value for value in component["publicExports"] if "/" not in value
    ]:
        module = importlib.import_module(namespace)
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        ), namespace
    public = importlib.import_module(entry["public_namespace"])
    assert [model.__name__ for model in public.PUBLIC_MODELS] == entry["models"]
    package = importlib.import_module(entry["public_namespace"].split(".")[0])
    assert package.__version__ == entry["artifact_version"]
    for export in entry["schema_exports"]:
        assert json.loads(
            (Path(package.__file__).parent / export).read_text()
        ) == json.loads((source / export).read_text()), export
    print(
        json.dumps(
            {
                "artifact": entry["distribution"],
                "version": entry["artifact_version"],
                "publicNamespace": entry["public_namespace"],
                "schemaExports": len(entry["schema_exports"]),
            }
        )
    )


if __name__ == "__main__":
    verify(
        json.loads(Path(sys.argv[1]).read_text()),
        json.loads(Path(sys.argv[2]).read_text()),
        Path(sys.argv[3]),
    )
