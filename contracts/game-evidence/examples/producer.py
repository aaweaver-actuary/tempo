"""Serialize hand-authored producer payloads; no schema-generated examples."""

import json
from pathlib import Path
from tempo_game_evidence_contracts import v1

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v1"


def produce():
    for fixture_path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(fixture_path.read_text())
        model = getattr(v1, fixture["model"])
        validated = v1.validate_contract(model, json.dumps(fixture["payload"]))
        yield {
            "case": fixture_path.stem,
            "model": fixture["model"],
            "payload": json.loads(v1.serialize_contract(validated)),
        }


if __name__ == "__main__":
    records = list(produce())
    if not records:
        raise SystemExit("zero producer examples")
    for record in records:
        print(json.dumps(record, sort_keys=True))
