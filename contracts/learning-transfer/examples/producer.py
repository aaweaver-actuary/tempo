"""Emit the reviewed synthetic v1 manifests from an installed contract."""
import json
from pathlib import Path
from tempo_learning_transfer_contracts import v1


def produce():
    directory = Path(__file__).resolve().parents[1] / "fixtures/v1"
    for path in sorted(directory.glob("*.json")):
        fixture = json.loads(path.read_text())
        model = getattr(v1, fixture["model"])
        value = v1.validate_contract(model, json.dumps(fixture["payload"]))
        yield {"case": path.stem, "model": fixture["model"],
               "payload": json.loads(v1.serialize_contract(value))}


if __name__ == "__main__":
    records = list(produce())
    if not records:
        raise SystemExit("zero producer examples")
    for record in records:
        print(json.dumps(record, sort_keys=True))
