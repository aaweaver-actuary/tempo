"""Pure identity inventory of caller-supplied Tactics bytes, not provenance."""

from dataclasses import asdict, dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class Asset:
    logical_path: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class Pack:
    pack_id: str
    asset: Asset
    record_count: int


@dataclass(frozen=True)
class Membership:
    pack_id: str
    index: int
    puzzle_id: str
    record_sha256: str


@dataclass(frozen=True)
class TacticsAssetInventory:
    contract_version: str
    catalog: Asset
    packs: tuple[Pack, ...]
    memberships: tuple[Membership, ...]
    puzzle_count: int
    catalog_version: int
    ordered_memberships_digest: str
    inventory_id: str
    status: str = "INVENTORIED"
    historical_authority: str = "UNPROVEN"


@dataclass(frozen=True)
class AssetInventoryBlocked:
    status: str
    code: str
    logical_path: str | None = None
    pack_id: str | None = None
    puzzle_id: str | None = None


def _block(code, path=None, pack=None, puzzle=None):
    return AssetInventoryBlocked("BLOCK", code, path, pack, puzzle)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _integer(value):
    number = int(value)
    if not -(2**63) <= number < 2**63:
        raise ValueError("integer out of range")
    return number


def _reject(_value):
    raise ValueError("unsupported JSON number")


def _parse(raw):
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_int=_integer,
        parse_float=_reject,
        parse_constant=_reject,
    )
    # Encoding the parsed tree rejects escaped lone surrogates, including keys.
    _canonical(value)
    return value


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _asset(path, raw):
    return Asset(path, len(raw), _digest(raw))


def _safe(path):
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and "\x00" not in path
        and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path)
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def inventory_tactics_asset_bytes(
    *, catalog_bytes: bytes, pack_assets: tuple[tuple[str, bytes], ...]
) -> TacticsAssetInventory | AssetInventoryBlocked:
    catalog_path = "data/tactics-catalog.json"
    try:
        catalog = _parse(catalog_bytes)
    except (ValueError, UnicodeError, RecursionError):
        return _block("INVALID_JSON", catalog_path)
    if not isinstance(catalog, dict):
        return _block("INVALID_CATALOG", catalog_path)
    if type(catalog.get("version")) is not int or catalog["version"] != 1:
        return _block("UNSUPPORTED_CATALOG_VERSION", catalog_path)
    if not isinstance(catalog.get("packs"), list):
        return _block("INVALID_CATALOG", catalog_path)
    entries = []
    ids, paths = set(), set()
    for entry in catalog["packs"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not entry["id"]
            or not isinstance(entry.get("asset"), str)
            or not entry["asset"]
        ):
            return _block("INVALID_CATALOG", catalog_path)
        pack, path = entry["id"], entry["asset"]
        if not _safe(path):
            return _block("UNSAFE_ASSET_PATH", path, pack)
        if pack in ids:
            return _block("DUPLICATE_PACK_ID", path, pack)
        if path in paths:
            return _block("DUPLICATE_ASSET_PATH", path, pack)
        ids.add(pack)
        paths.add(path)
        entries.append((pack, path))
    supplied = {}
    for path, raw in pack_assets:
        if path in supplied:
            return _block("DUPLICATE_ASSET_PATH", path)
        supplied[path] = raw
    for pack, path in entries:
        if path not in supplied:
            return _block("MISSING_ASSET", path, pack)
    for path in supplied:
        if path not in paths:
            return _block("UNEXPECTED_ASSET", path)
    packs, memberships, puzzles = [], [], set()
    for pack, path in entries:
        raw = supplied[path]
        try:
            records = _parse(raw)
        except (ValueError, UnicodeError, RecursionError):
            return _block("INVALID_JSON", path, pack)
        if not isinstance(records, list):
            return _block("INVALID_PACK", path, pack)
        packs.append(Pack(pack, _asset(path, raw), len(records)))
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                return _block("INVALID_PUZZLE_RECORD", path, pack)
            puzzle, fen, moves = (
                record.get("PuzzleId"),
                record.get("FEN"),
                record.get("Moves"),
            )
            if (
                not isinstance(puzzle, str)
                or not puzzle
                or not isinstance(fen, str)
                or not (
                    isinstance(moves, str)
                    or (
                        isinstance(moves, list)
                        and all(isinstance(m, str) and m for m in moves)
                    )
                )
            ):
                return _block("INVALID_PUZZLE_RECORD", path, pack)
            if puzzle in puzzles:
                return _block("DUPLICATE_PUZZLE_ID", path, pack, puzzle)
            puzzles.add(puzzle)
            memberships.append(
                Membership(pack, index, puzzle, _digest(_canonical(record)))
            )
    memberships = tuple(memberships)
    membership_digest = _digest(_canonical([asdict(m) for m in memberships]))
    envelope = dict(
        contract_version="T-ASSET.inventory.v1",
        catalog=asdict(_asset(catalog_path, catalog_bytes)),
        packs=[asdict(p) for p in packs],
        memberships=[asdict(m) for m in memberships],
        puzzle_count=len(memberships),
        catalog_version=1,
        ordered_memberships_digest=membership_digest,
    )
    return TacticsAssetInventory(
        envelope["contract_version"],
        _asset(catalog_path, catalog_bytes),
        tuple(packs),
        memberships,
        len(memberships),
        1,
        membership_digest,
        _digest(_canonical(envelope)),
    )
