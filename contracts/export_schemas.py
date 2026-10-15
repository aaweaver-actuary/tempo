"""Export or check deterministic schemas from installed public model namespaces."""

import argparse
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGES = (
    "learning",
    "content-publication",
    "game-evidence",
    "intelligence",
    "analysis-jobs",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--package", choices=PACKAGES)
    arguments = parser.parse_args()
    count = 0
    catalog = json.loads((ROOT / "catalog.json").read_text())
    for package in ([arguments.package] if arguments.package else PACKAGES):
        namespace = "tempo_" + package.replace("-", "_") + "_contracts"
        entry = next(item for item in catalog["contracts"] if item["root"] == package)
        for version in entry.get("supported_wire_versions", [entry["wire_version"]]):
            count += export_namespace(package, namespace, version, arguments.check)
    if count == 0:
        raise SystemExit("zero schema exports")
    print(f"{count} schemas {'verified' if arguments.check else 'exported'}")


def export_namespace(package, namespace, version, check):
    public = importlib.import_module(namespace + f".v{version}")
    directory = ROOT / package / f"schemas/v{version}"
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    expected_names = {
        model.__name__ + ".schema.json" for model in public.PUBLIC_MODELS
    }
    unexpected = {
        path.name for path in directory.glob("*.schema.json")
    } - expected_names
    if unexpected:
        raise SystemExit(f"unexpected schemas for {package}: {sorted(unexpected)}")
    for model in public.PUBLIC_MODELS:
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = (
            f"https://tempo.invalid/contracts/{package}/v{version}/{model.__name__}.schema.json"
        )
        encoded = (
            json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
        path = directory / (model.__name__ + ".schema.json")
        if check:
            if not path.exists() or path.read_text() != encoded:
                raise SystemExit(f"schema drift: {path}")
        else:
            path.write_text(encoded)
    return len(public.PUBLIC_MODELS)

if __name__ == "__main__":
    main()
