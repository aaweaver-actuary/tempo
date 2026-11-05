"""Bounded fixture-root capture and prospective recorded Tactics generation."""

from copy import deepcopy
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import stat
from threading import RLock
import uuid

from .tactics_asset_archive import (
    AssetGenerationBlocked,
    _hash,
    _open_absolute,
    archive_tactics_generation,
    blocked,
    read_archived_blob,
)
from .tactics_asset_inventory import (
    AssetInventoryBlocked,
    inventory_tactics_asset_bytes,
)


@dataclass(frozen=True)
class AssetCaptureLimits:
    max_catalog_bytes: int
    max_pack_bytes: int
    max_total_bytes: int
    max_packs: int
    max_records: int
    max_path_components: int


@dataclass(frozen=True)
class RootIdentity:
    configured_absolute_path: str
    opened_device: int
    opened_inode: int


@dataclass(frozen=True)
class FilesAtCapture:
    status: str
    inventory: object
    root_identity: RootIdentity
    captured_observation_id: str
    source_atomicity: str = "UNPROVEN"


@dataclass(frozen=True)
class ServedGenerationObservation:
    status: str
    process_incarnation: str
    instance_nonce: str
    inventory_id: str
    catalog_raw_digest: str | None
    ordered_loaded_pack_raw_digests: tuple[tuple[str, str], ...]
    membership_inventory_id: str | None
    loaded_cache_states: tuple[str, ...]
    history: str = "UNPROVEN"
    database_binding: None = None
    installed_authority: str = "UNPROVEN"


def _stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read_relative(root, path, limit, max_components):
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
        or "\\" in path
        or "\x00" in path
    ):
        return blocked("UNSAFE_ASSET_PATH", path)
    if len(parts) > max_components:
        return blocked("CAPTURE_LIMIT_EXCEEDED", path)
    fd = os.dup(root)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        leaf = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode):
                return blocked("UNSAFE_ASSET_PATH", path)
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(leaf, min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(leaf)
            if _stamp(before) != _stamp(after):
                return blocked("SOURCE_CHANGED", path)
            if len(data) > limit:
                return blocked("CAPTURE_LIMIT_EXCEEDED", path)
            return bytes(data), _stamp(after)
        finally:
            os.close(leaf)
    except FileNotFoundError:
        return blocked("MISSING_ASSET", path)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return blocked("UNSAFE_ASSET_PATH", path)
        return blocked("SOURCE_CHANGED", path)
    finally:
        os.close(fd)


_CONSTRUCTION_KEY = object()


class RecordedTacticsGeneration:
    def __init__(
        self,
        inventory,
        capture,
        receipt,
        process_incarnation,
        catalog_bytes,
        pack_assets,
        *,
        _key=None,
    ):
        if _key is not _CONSTRUCTION_KEY:
            raise ValueError("generation must come from capture and readback")
        self.inventory, self.files_at_capture, self.archive_receipt = (
            inventory,
            capture,
            receipt,
        )
        self._catalog_bytes, self._pack_assets = catalog_bytes, dict(pack_assets)
        self._process, self._nonce = process_incarnation, uuid.uuid4().hex
        self._lock = RLock()
        self._catalog_cache = None
        self._pack_cache = {}
        self._membership_cache = None

    def _entry(self, entry, digest):
        if not isinstance(entry, tuple) or len(entry) != 3:
            return blocked(
                "CACHE_PROVENANCE_UNRECORDED", inventory=self.inventory.inventory_id
            )
        if entry[:2] != (self.inventory.inventory_id, digest):
            return blocked(
                "CACHE_GENERATION_MISMATCH", inventory=self.inventory.inventory_id
            )
        return None

    def catalog(self):
        with self._lock:
            digest = self.inventory.catalog.sha256
            raw = read_archived_blob(
                self.archive_receipt, digest, self.inventory.catalog.byte_length
            )
            if raw != self._catalog_bytes:
                return blocked(
                    "ARCHIVE_READBACK_FAILED", inventory=self.inventory.inventory_id
                )
            if self._catalog_cache is not None:
                failure = self._entry(self._catalog_cache, digest)
                if failure:
                    return failure
            else:
                self._catalog_cache = (
                    self.inventory.inventory_id,
                    digest,
                    json.loads(self._catalog_bytes),
                )
            return deepcopy(self._catalog_cache[2])

    def pack_records(self, pack_id):
        with self._lock:
            pack = next((p for p in self.inventory.packs if p.pack_id == pack_id), None)
            if pack is None:
                return blocked(
                    "UNKNOWN_PACK", pack=pack_id, inventory=self.inventory.inventory_id
                )
            raw = read_archived_blob(
                self.archive_receipt, pack.asset.sha256, pack.asset.byte_length
            )
            if raw != self._pack_assets[pack.asset.logical_path]:
                return blocked(
                    "ARCHIVE_READBACK_FAILED",
                    pack.asset.logical_path,
                    pack_id,
                    self.inventory.inventory_id,
                )
            if pack_id in self._pack_cache:
                failure = self._entry(self._pack_cache[pack_id], pack.asset.sha256)
                if failure:
                    return failure
            else:
                self._pack_cache[pack_id] = (
                    self.inventory.inventory_id,
                    pack.asset.sha256,
                    json.loads(raw),
                )
            return deepcopy(self._pack_cache[pack_id][2])

    def puzzle_membership(self):
        with self._lock:
            if (
                read_archived_blob(
                    self.archive_receipt,
                    self.inventory.catalog.sha256,
                    self.inventory.catalog.byte_length,
                )
                != self._catalog_bytes
            ):
                return blocked(
                    "ARCHIVE_READBACK_FAILED", inventory=self.inventory.inventory_id
                )
            if self._membership_cache is not None:
                failure = self._entry(
                    self._membership_cache, self.inventory.ordered_memberships_digest
                )
                if failure:
                    return failure
                for pack in self.inventory.packs:
                    records = self.pack_records(pack.pack_id)
                    if isinstance(records, AssetGenerationBlocked):
                        return records
            else:
                result = {}
                for pack in self.inventory.packs:
                    records = self.pack_records(pack.pack_id)
                    if isinstance(records, AssetGenerationBlocked):
                        return records
                    for record in records:
                        result[record["PuzzleId"]] = (pack.pack_id, record)
                self._membership_cache = (
                    self.inventory.inventory_id,
                    self.inventory.ordered_memberships_digest,
                    result,
                )
            return deepcopy(self._membership_cache[2])

    def observation(self):
        with self._lock:
            if (
                self._catalog_cache is None
                and not self._pack_cache
                and self._membership_cache is None
            ):
                return blocked(
                    "CACHE_PROVENANCE_UNRECORDED", inventory=self.inventory.inventory_id
                )
            if self._catalog_cache is not None:
                if (
                    read_archived_blob(
                        self.archive_receipt,
                        self.inventory.catalog.sha256,
                        self.inventory.catalog.byte_length,
                    )
                    != self._catalog_bytes
                ):
                    return blocked(
                        "ARCHIVE_READBACK_FAILED", inventory=self.inventory.inventory_id
                    )
                failure = self._entry(
                    self._catalog_cache, self.inventory.catalog.sha256
                )
                if failure:
                    return failure
            for pack_id, entry in self._pack_cache.items():
                pack = next(
                    (p for p in self.inventory.packs if p.pack_id == pack_id), None
                )
                if pack is None:
                    return blocked(
                        "CACHE_PROVENANCE_UNRECORDED",
                        pack=pack_id,
                        inventory=self.inventory.inventory_id,
                    )
                if (
                    read_archived_blob(
                        self.archive_receipt, pack.asset.sha256, pack.asset.byte_length
                    )
                    != self._pack_assets[pack.asset.logical_path]
                ):
                    return blocked(
                        "ARCHIVE_READBACK_FAILED",
                        pack.asset.logical_path,
                        pack_id,
                        self.inventory.inventory_id,
                    )
                failure = self._entry(entry, pack.asset.sha256)
                if failure:
                    return failure
            if self._membership_cache is not None:
                failure = self._entry(
                    self._membership_cache, self.inventory.ordered_memberships_digest
                )
                if failure:
                    return failure
            loaded = tuple(
                (p.pack_id, p.asset.sha256)
                for p in self.inventory.packs
                if p.pack_id in self._pack_cache
            )
            states = tuple(
                name
                for name, present in (
                    ("catalog", self._catalog_cache is not None),
                    ("packs", bool(loaded)),
                    ("membership", self._membership_cache is not None),
                )
                if present
            )
            return ServedGenerationObservation(
                "PROSPECTIVE_SERVED",
                self._process,
                self._nonce,
                self.inventory.inventory_id,
                self.inventory.catalog.sha256
                if self._catalog_cache is not None
                else None,
                loaded,
                self.inventory.inventory_id
                if self._membership_cache is not None
                else None,
                states,
            )


def capture_recorded_tactics_generation(
    *,
    configured_root: Path,
    archive_root: Path,
    workspace_id: str,
    process_incarnation: str,
    limits: AssetCaptureLimits,
):
    if (
        not isinstance(limits, AssetCaptureLimits)
        or any(type(v) is not int or v <= 0 for v in vars(limits).values())
        or not isinstance(workspace_id, str)
        or not workspace_id
        or not isinstance(process_incarnation, str)
        or not process_incarnation
    ):
        return blocked("CAPTURE_LIMIT_EXCEEDED")
    root = None
    try:
        root = _open_absolute(configured_root)
        before = os.fstat(root)
        identity = RootIdentity(str(configured_root), before.st_dev, before.st_ino)
        catalog_path = "data/tactics-catalog.json"
        read = _read_relative(
            root, catalog_path, limits.max_catalog_bytes, limits.max_path_components
        )
        if isinstance(read, AssetGenerationBlocked):
            return read
        catalog_bytes, catalog_stamp = read
        initial = inventory_tactics_asset_bytes(
            catalog_bytes=catalog_bytes, pack_assets=()
        )
        if (
            isinstance(initial, AssetInventoryBlocked)
            and initial.code != "MISSING_ASSET"
        ):
            return blocked(initial.code, initial.logical_path, initial.pack_id)
        catalog = json.loads(catalog_bytes)
        entries = [(p["id"], p["asset"]) for p in catalog["packs"]]
        if len(entries) > limits.max_packs:
            return blocked("CAPTURE_LIMIT_EXCEEDED")
        total = len(catalog_bytes)
        if total > limits.max_total_bytes:
            return blocked("CAPTURE_LIMIT_EXCEEDED")
        assets, stamps = (
            [],
            [(catalog_path, catalog_bytes, catalog_stamp, limits.max_catalog_bytes)],
        )
        for pack, path in entries:
            read = _read_relative(
                root,
                path,
                min(limits.max_pack_bytes, limits.max_total_bytes - total),
                limits.max_path_components,
            )
            if isinstance(read, AssetGenerationBlocked):
                return blocked(read.code, path, pack)
            raw, stamp = read
            total += len(raw)
            assets.append((path, raw))
            stamps.append((path, raw, stamp, limits.max_pack_bytes))
        assets = tuple(assets)
        inventory = inventory_tactics_asset_bytes(
            catalog_bytes=catalog_bytes, pack_assets=assets
        )
        if isinstance(inventory, AssetInventoryBlocked):
            return blocked(inventory.code, inventory.logical_path, inventory.pack_id)
        if inventory.puzzle_count > limits.max_records:
            return blocked("CAPTURE_LIMIT_EXCEEDED", inventory=inventory.inventory_id)
        for path, raw, stamp, limit in stamps:
            again = _read_relative(root, path, limit, limits.max_path_components)
            if isinstance(again, AssetGenerationBlocked) or again != (raw, stamp):
                return blocked("SOURCE_CHANGED", path, inventory=inventory.inventory_id)
        after = os.fstat(root)
        try:
            reopened = _open_absolute(configured_root)
            try:
                path_stat = os.fstat(reopened)
            finally:
                os.close(reopened)
        except (OSError, ValueError):
            return blocked("SOURCE_CHANGED", inventory=inventory.inventory_id)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or (
            before.st_dev,
            before.st_ino,
        ) != (path_stat.st_dev, path_stat.st_ino):
            return blocked("SOURCE_CHANGED", inventory=inventory.inventory_id)
        receipt = archive_tactics_generation(
            archive_root=archive_root,
            catalog_bytes=catalog_bytes,
            pack_assets=assets,
            inventory=inventory,
        )
        if isinstance(receipt, AssetGenerationBlocked):
            return receipt
        capture = FilesAtCapture(
            "FILES_CAPTURED", inventory, identity, uuid.uuid4().hex
        )
        return RecordedTacticsGeneration(
            inventory,
            capture,
            receipt,
            process_incarnation,
            catalog_bytes,
            assets,
            _key=_CONSTRUCTION_KEY,
        )
    except (OSError, ValueError, AttributeError):
        return blocked("UNSAFE_ASSET_PATH")
    finally:
        if root is not None:
            os.close(root)
