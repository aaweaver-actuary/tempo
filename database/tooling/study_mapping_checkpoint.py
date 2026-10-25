"""Check supplied #389 implementation-release evidence for consistency only.

This read-only utility binds the supplied authority fingerprint, authorized
snapshot, field/identity map and independently reviewed golden snapshots to one
exact approval packet. It reuses #388's normalized snapshot contract. It never
infers aliases, accesses an application, authenticates a reviewer/authorization,
releases a converter, or establishes final replay/interruption/cutover acceptance.
The caller must independently establish the authority and review of all inputs.

Run with ``python -m database.tooling.study_mapping_checkpoint CHECKPOINT`` and
one ``--artifact NAME PATH`` per ARTIFACT_NAMES entry. Paths and private values
are never emitted. Exit 0 means consistent supplied evidence, not authorization;
2 means invalid, incomplete or inconsistent. Synthetic packets can exercise the
reader but are rejected by ``--require-actual``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__:
    from .study_snapshot_compare import SnapshotError, compare_snapshots
else:
    from study_snapshot_compare import SnapshotError, compare_snapshots


LEAF_NAMES = (
    "content_identity",
    "reviews_schedules",
    "server_receipts",
    "browser_intents",
)
ARTIFACT_NAMES = (
    "authority_fingerprint",
    "authorized_snapshot",
    "snapshot_authorization",
    "mapping",
    "golden_before",
    "golden_after",
    "golden_extraction",
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(ValueError):
    """Supplied evidence cannot establish even checkpoint consistency."""


def _exact_fields(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        raise CheckpointError(f"{label}: expected exactly the required fields")


def _identity(value):
    if type(value) is not str or not value or value != value.strip():
        raise CheckpointError("review identities must be nonempty exact strings")


def validate_checkpoint(
    checkpoint: object,
    artifact_digests: object,
    golden_before: object,
    golden_after: object,
    *,
    leaf_source_digests: object,
    required_leaf: str | None = None,
    require_actual: bool = False,
) -> dict[str, object]:
    """Validate supplied digests/review scope and golden equality, without writes.

    ``artifact_digests`` contains SHA-256 of exact supplied bytes, not normalized
    JSON or a second copy of the claimed bindings. Computing these and checking
    trusted provenance is the caller's responsibility. The CLI computes bytes'
    hashes itself. ``leaf_source_digests`` separately binds complete source
    packets using the composition root's canonical JSON contract, including
    approved row-key decisions. No input is mutated. No replay receipt is required here:
    replay and interruption remain a later, mandatory final acceptance gate.
    ``actual_release`` means supplied evidence is eligible for an independent
    authorization check, never that this utility authenticated or issued one.
    """
    _exact_fields(checkpoint, (
        "schema_version", "evidence_kind", "bindings", "review",
        "approved_leafs", "unresolved",
    ), "checkpoint")
    if type(require_actual) is not bool:
        raise CheckpointError("require_actual must be an explicit boolean")
    if type(checkpoint["schema_version"]) is not int or checkpoint["schema_version"] != 1:
        raise CheckpointError("schema_version must be integer 1")
    kind = checkpoint["evidence_kind"]
    if kind not in ("actual", "synthetic") or type(kind) is not str:
        raise CheckpointError("evidence_kind must be actual or synthetic")
    if require_actual and kind != "actual":
        raise CheckpointError("synthetic evidence cannot satisfy the actual checkpoint")
    if type(checkpoint["unresolved"]) is not list or checkpoint["unresolved"]:
        raise CheckpointError("unresolved mapping decisions block the checkpoint")

    fields = {f"{name}_sha256" for name in ARTIFACT_NAMES} | {"leaf_source_sha256"}
    bindings = checkpoint["bindings"]
    _exact_fields(bindings, fields, "bindings")
    _exact_fields(artifact_digests, ARTIFACT_NAMES, "artifact digests")
    for name in ARTIFACT_NAMES:
        claimed, observed = bindings[f"{name}_sha256"], artifact_digests[name]
        if any(type(value) is not str or _DIGEST.fullmatch(value) is None
               for value in (claimed, observed)):
            raise CheckpointError("artifact digests must be lowercase SHA-256")
        if claimed != observed:
            raise CheckpointError("supplied artifact does not match its approved binding")

    leafs = checkpoint["approved_leafs"]
    if type(leafs) is not list or not leafs or any(
        type(leaf) is not str or leaf not in LEAF_NAMES for leaf in leafs
    ) or len(set(leafs)) != len(leafs):
        raise CheckpointError("approved_leafs must contain unique known converter leafs")
    if required_leaf is not None and (
        required_leaf not in LEAF_NAMES or required_leaf not in leafs
    ):
        raise CheckpointError("requested converter leaf is outside the supplied approval")
    _exact_fields(bindings["leaf_source_sha256"], leafs, "approved leaf source digests")
    _exact_fields(leaf_source_digests, leafs, "supplied leaf source digests")
    for leaf in leafs:
        claimed = bindings["leaf_source_sha256"][leaf]
        observed = leaf_source_digests[leaf]
        if any(type(value) is not str or _DIGEST.fullmatch(value) is None
               for value in (claimed, observed)) or claimed != observed:
            raise CheckpointError("leaf source packet does not match its approved binding")

    review = checkpoint["review"]
    _exact_fields(review, (
        "mapping_owner", "golden_extractor", "independent_approver", "converter_owners",
    ), "review")
    _exact_fields(review["converter_owners"], leafs, "converter owners")
    owner_values = list(review["converter_owners"].values())
    for identity in [review["mapping_owner"], review["golden_extractor"],
                     review["independent_approver"], *owner_values]:
        _identity(identity)
    approver = review["independent_approver"]
    extractor = review["golden_extractor"]
    if approver in [review["mapping_owner"], extractor, *owner_values]:
        raise CheckpointError("the golden approver must be independent of preparation")
    if extractor in owner_values:
        raise CheckpointError("golden extraction must be independent of converter ownership")
    if len(set(owner_values)) != len(owner_values):
        raise CheckpointError("approved converter leafs must have distinct named owners")

    try:
        comparison = compare_snapshots(golden_before, golden_after)
    except (SnapshotError, RecursionError):
        raise CheckpointError("golden snapshots must satisfy the exact normalized contract") from None
    if not comparison["equal"]:
        raise CheckpointError("golden snapshots differ; the checkpoint remains blocked")
    return {
        "consistent": True,
        "evidence_kind": kind,
        "approved_leafs": sorted(leafs),
        "authority_fingerprint_sha256": bindings["authority_fingerprint_sha256"],
        "leaf_source_sha256": dict(bindings["leaf_source_sha256"]),
        "checkpoint_sha256": hashlib.sha256(json.dumps(
            checkpoint, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")).hexdigest(),
        "actual_release": require_actual and kind == "actual",
        "authenticated": False,
        "disclaimer": "supplied evidence consistency; authorization and reviewer identity require external verification",
        "scope": "supplied implementation-release evidence only",
        "final_acceptance": "unverified",
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointError("JSON object members must be unique")
        result[key] = value
    return result


def _reject_constant(_value):
    raise CheckpointError("JSON numbers must be finite")


def _decode_json(payload):
    try:
        value = json.loads(payload.decode("utf-8"),
                           object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        # JSON's exponent syntax can overflow Python floats without invoking
        # parse_constant (for example 1e999); reject that path as well.
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise CheckpointError("input must be readable strict UTF-8 JSON") from None


def _read_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        raise CheckpointError("input must be readable") from None


def _file_digest(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as artifact:
            for block in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        raise CheckpointError("artifact must be readable") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--artifact", nargs=2, action="append", default=[],
                        metavar=("NAME", "PATH"))
    parser.add_argument("--leaf", choices=LEAF_NAMES)
    parser.add_argument("--leaf-source", nargs=2, action="append", default=[],
                        metavar=("NAME", "PATH"))
    parser.add_argument("--require-actual", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        artifacts = {}
        for name, path in arguments.artifact:
            if name not in ARTIFACT_NAMES or name in artifacts:
                raise CheckpointError("artifact names must be known and supplied once")
            artifacts[name] = path
        if set(artifacts) != set(ARTIFACT_NAMES):
            raise CheckpointError("all checkpoint artifacts must be supplied explicitly")
        # Parse and hash the same golden bytes; a second file read could compare
        # different content from the bytes bound by the reviewer packet.
        golden_bytes = {name: _read_bytes(artifacts[name])
                        for name in ("golden_before", "golden_after")}
        digests = {name: hashlib.sha256(golden_bytes[name]).hexdigest()
                   if name in golden_bytes else _file_digest(path)
                   for name, path in artifacts.items()}
        leaf_digests = {}
        for leaf, path in arguments.leaf_source:
            if leaf not in LEAF_NAMES or leaf in leaf_digests:
                raise CheckpointError("leaf source names must be known and supplied once")
            source = _decode_json(_read_bytes(path))
            if type(source) is not dict:
                raise CheckpointError("leaf sources must be complete JSON objects")
            canonical = json.dumps(source, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf-8")
            leaf_digests[leaf] = hashlib.sha256(canonical).hexdigest()
        result = validate_checkpoint(
            _decode_json(_read_bytes(arguments.checkpoint)), digests,
            _decode_json(golden_bytes["golden_before"]),
            _decode_json(golden_bytes["golden_after"]),
            leaf_source_digests=leaf_digests,
            required_leaf=arguments.leaf,
            require_actual=arguments.require_actual,
        )
    except (CheckpointError, RecursionError):
        print(json.dumps({"error": "checkpoint evidence is invalid, incomplete or inconsistent"}),
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
