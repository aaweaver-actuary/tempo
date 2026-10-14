from pathlib import Path
import importlib.util
import json

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def load_fixture(name):
    return json.loads(
        (PACKAGE_ROOT / "fixtures" / "v1" / (name + ".json")).read_text()
    )["payload"]


def load_reference():
    specification = importlib.util.spec_from_file_location(
        "reference_" + PACKAGE_ROOT.name.replace("-", "_"),
        PACKAGE_ROOT / "examples" / "reference.py",
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module
