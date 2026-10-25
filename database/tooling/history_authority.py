"""Bind a history leaf's supplied packet to an independently approved checkpoint.

Pure preparation for #358/#389/#267. This module does not observe an authority,
authenticate an approver, map identities, write history, or issue cutover receipts.
The composition root must inject the independent approval capability. No live
implementation of that capability is supplied here.
"""

import copy
import hashlib
import json
import math
import re


LEAVES = frozenset((
    "content_identity", "reviews_schedules", "server_receipts", "browser_intents",
))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class AuthorityError(ValueError):
    """Supplied evidence cannot authorize this exact source packet."""


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise AuthorityError("source packet must contain finite JSON values")


def canonical_packet_sha256(packet):
    """Hash strict JSON using the frozen #389 canonical packet encoding.

    Readers must reject duplicate JSON members before constructing a dictionary.
    This function receives an already parsed value and cannot recover duplicates.
    Arrays retain their order; object member order has no meaning.
    """
    try:
        _json_value(packet)
        encoded = json.dumps(
            packet, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AuthorityError("source packet must contain finite UTF-8 JSON values") from None


def _digest(value):
    return type(value) is str and _SHA256.fullmatch(value) is not None


def approved_source_validator(checkpoint, leaf, *, independent_approval):
    """Return a leaf-scoped approval callback for the exact frozen source packet.

    ``checkpoint`` is the public result of study_mapping_checkpoint's validator.
    Its ``actual_release`` describes supplied-evidence eligibility, not trust.
    ``independent_approval`` must independently verify that exact checkpoint and
    return the boolean True. A JSON flag or a truthy value is insufficient.

    Leaf converters invoke the returned callback with their entire source packet,
    before projection. The callback returns True or raises a redacted error.
    This capability approves source preparation only, never target writes/replay.
    """
    if type(leaf) is not str or leaf not in LEAVES:
        raise AuthorityError("unknown converter leaf")
    if type(checkpoint) is not dict:
        raise AuthorityError("checkpoint must be supplied")
    try:
        frozen = copy.deepcopy(checkpoint)
        canonical_packet_sha256(frozen)
    except (TypeError, ValueError, RecursionError):
        raise AuthorityError("checkpoint must contain finite JSON values") from None
    if (
        frozen.get("consistent") is not True
        or frozen.get("actual_release") is not True
        or frozen.get("evidence_kind") != "actual"
        or frozen.get("authenticated") is not False
        or frozen.get("final_acceptance") != "unverified"
    ):
        raise AuthorityError("actual implementation-release checkpoint is required")
    approved = frozen.get("approved_leafs")
    hashes = frozen.get("leaf_source_sha256")
    authority_digest = frozen.get("authority_fingerprint_sha256")
    if (
        type(approved) is not list or not approved
        or any(type(name) is not str or name not in LEAVES for name in approved)
        or len(set(approved)) != len(approved)
        or leaf not in approved
        or type(hashes) is not dict or set(hashes) != set(approved)
        or any(not _digest(value) for value in hashes.values())
        or not _digest(authority_digest)
        or not _digest(frozen.get("checkpoint_sha256"))
    ):
        raise AuthorityError("checkpoint does not bind the requested leaf")
    if not callable(independent_approval):
        raise AuthorityError("independent approval capability is required")
    try:
        approved_independently = independent_approval(copy.deepcopy(frozen))
    except Exception:
        raise AuthorityError("independent approval did not verify the checkpoint") from None
    if approved_independently is not True:
        raise AuthorityError("independent approval did not verify the checkpoint")
    source_digest = hashes[leaf]

    def approve(source):
        if (
            type(source) is not dict
            or type(source.get("schema_version")) is not int
            or source["schema_version"] != 1
            or type(source.get("unresolved")) is not list
            or source["unresolved"]
            or type(source.get("authority")) is not dict
            or source["authority"] != {"fingerprint_sha256": authority_digest}
        ):
            raise AuthorityError("source authority binding is missing or unresolved")
        if canonical_packet_sha256(source) != source_digest:
            raise AuthorityError("source packet differs from the approved leaf input")
        return True

    return approve
