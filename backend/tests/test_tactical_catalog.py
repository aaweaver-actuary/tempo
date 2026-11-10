import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "public/data"


def test_existing_tactical_puzzles_survive_splitting_into_25_card_packs():
    manifest = json.loads((DATA / "tactics-catalog.json").read_text())
    records = [
        record
        for pack in manifest["packs"]
        for record in json.loads((DATA.parent / pack["asset"]).read_text())
    ]
    by_identity = {record["PuzzleId"]: record for record in records}
    for original in json.loads((DATA / "tactics-decks.json").read_text()):
        migrated = by_identity[original["PuzzleId"]]
        for field in ("FEN", "Moves", "Rating", "Themes", "GameUrl"):
            assert migrated[field] == original[field]
        assert migrated["LegacyDeckId"] == original["DeckId"]
        assert migrated["LegacyDeckPosition"] == original["DeckPosition"]


def test_expanded_tactical_catalog_contains_every_requested_theme_and_complete_pack():
    from app.services.puzzles import validate_puzzle_record

    manifest = json.loads((DATA / "tactics-catalog.json").read_text())
    assert len(manifest["themes"]) == 47
    assert len(manifest["packs"]) == 692
    identities = set()
    for pack in manifest["packs"]:
        records = json.loads((DATA.parent / pack["asset"]).read_text())
        assert len(records) == pack["count"] == 25
        assert [record["DeckPosition"] for record in records] == list(range(1, 26))
        for record in records:
            assert record["DeckId"] == pack["id"]
            assert record["PuzzleId"] not in identities
            identities.add(record["PuzzleId"])
            validate_puzzle_record(record)
    assert len(identities) == 17300
    assert manifest["source"]["sha256"]
