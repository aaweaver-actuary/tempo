import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPECIFICATION = importlib.util.spec_from_file_location(
    "tempo_contract_ownership_fixture", ROOT / "fixture.py"
)
FIXTURE_MODULE = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(FIXTURE_MODULE)


@pytest.fixture(scope="session")
def ownership():
    fixture = FIXTURE_MODULE.OwnershipFixture()
    try:
        fixture.start()
        print("Native ownership image: " + fixture.image_id, flush=True)
        yield fixture
    finally:
        fixture.close()
