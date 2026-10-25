"""Synthetic tests; no actual mapping, authorization or replay acceptance."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database.tooling import study_mapping_checkpoint as checkpoint


def snapshot():
    return {
        "schema_version": 1, "workspace_id": "synthetic-only",
        "unresolved": [],
        "entities": {name: [] for name in (
            "cards", "reviews", "schedules", "introductions", "accepted_definitions",
            "queue_attempts", "operation_receipts", "browser_intents",
        )},
    }


def packet(kind="synthetic"):
    digests = {name: hashlib.sha256(name.encode()).hexdigest()
               for name in checkpoint.ARTIFACT_NAMES}
    source_digests = {"content_identity": hashlib.sha256(b"source").hexdigest()}
    return {
        "schema_version": 1,
        "evidence_kind": kind,
        "bindings": {**{f"{name}_sha256": value for name, value in digests.items()},
                     "leaf_source_sha256": source_digests.copy()},
        "review": {
            "mapping_owner": "synthetic-mapper",
            "golden_extractor": "synthetic-extractor",
            "independent_approver": "synthetic-reviewer",
            "converter_owners": {"content_identity": "synthetic-converter"},
        },
        "approved_leafs": ["content_identity"],
        "unresolved": [],
    }, digests, source_digests


class StudyMappingCheckpointTests(unittest.TestCase):
    def validate(self, value=None, digests=None, leaf_digests=None, before=None, after=None, **kwargs):
        defaults = packet()
        return checkpoint.validate_checkpoint(
            defaults[0] if value is None else value,
            defaults[1] if digests is None else digests,
            snapshot() if before is None else before,
            snapshot() if after is None else after,
            leaf_source_digests=defaults[2] if leaf_digests is None else leaf_digests,
            **kwargs,
        )

    def test_repeated_synthetic_validation_is_nonmutating_and_never_actual_release(self):
        value, digests, leaf_digests = packet()
        original = copy.deepcopy((value, digests, leaf_digests))
        result = self.validate(value, digests, leaf_digests)
        self.assertEqual(result, self.validate(value, digests, leaf_digests))
        self.assertEqual((value, digests, leaf_digests), original)
        self.assertTrue(result["consistent"])
        self.assertFalse(result["actual_release"])
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["final_acceptance"], "unverified")
        self.assertNotIn("synthetic-reviewer", json.dumps(result))

    def test_synthetic_cannot_satisfy_actual_requirement(self):
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(require_actual=True)

    def test_actual_label_only_establishes_supplied_eligibility_when_explicitly_requested(self):
        value, digests, leaf_digests = packet("actual")
        self.assertFalse(self.validate(value, digests, leaf_digests)["actual_release"])
        result = self.validate(value, digests, leaf_digests, require_actual=True)
        self.assertTrue(result["actual_release"])
        self.assertFalse(result["authenticated"])
        self.assertIn("external verification", result["disclaimer"])

    def test_result_identifies_entire_review_packet_for_external_approval(self):
        value, digests, leaf_digests = packet()
        result = self.validate(value, digests, leaf_digests)
        expected = hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(result["checkpoint_sha256"], expected)
        value["review"]["independent_approver"] = "another-independent-label"
        self.assertNotEqual(self.validate(value, digests, leaf_digests)["checkpoint_sha256"], expected)

    def test_actual_requirement_does_not_coerce_truthy_values(self):
        value, _, _ = packet("actual")
        for requirement in (1, "false", None):
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value, require_actual=requirement)

    def test_converter_replay_is_not_required_before_implementation(self):
        value, _, _ = packet("actual")
        self.assertNotIn("replay", value)
        self.assertTrue(self.validate(value, require_actual=True)["consistent"])
        value["replay"] = "invented"
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(value)

    def test_every_approved_artifact_binding_is_required_and_exact(self):
        for name in checkpoint.ARTIFACT_NAMES:
            with self.subTest(name=name):
                value, digests, _ = packet()
                value["bindings"][f"{name}_sha256"] = "0" * 64
                with self.assertRaises(checkpoint.CheckpointError):
                    self.validate(value, digests)
                value, digests, _ = packet()
                del digests[name]
                with self.assertRaises(checkpoint.CheckpointError):
                    self.validate(value, digests)

    def test_digest_encoding_is_strict_and_does_not_coerce_types(self):
        for invalid in (True, None, 64, "", "A" * 64, "0" * 63, "0" * 64 + "\n"):
            with self.subTest(invalid=invalid):
                value, _, _ = packet()
                value["bindings"]["mapping_sha256"] = invalid
                with self.assertRaises(checkpoint.CheckpointError):
                    self.validate(value)

    def test_unresolved_mapping_decisions_fail_closed(self):
        for unresolved in (["synthetic-unapproved-alias"], None, {}, False):
            value, _, _ = packet()
            value["unresolved"] = unresolved
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)

    def test_complete_leaf_packet_digest_is_bound_separately_from_golden(self):
        value, digests, leaf_digests = packet()
        leaf_digests["content_identity"] = "0" * 64
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(value, digests, leaf_digests)

    def test_leaf_source_and_owner_maps_cover_exact_approved_scope(self):
        for target in ("approved_digest", "observed_digest", "owners"):
            value, digests, leaf_digests = packet()
            selected = {
                "approved_digest": value["bindings"]["leaf_source_sha256"],
                "observed_digest": leaf_digests,
                "owners": value["review"]["converter_owners"],
            }[target]
            selected["browser_intents"] = "0" * 64
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value, digests, leaf_digests)

    def test_unknown_duplicate_empty_and_unapproved_leafs_fail_closed(self):
        for leafs in ([], ["invented"], ["content_identity"] * 2, None, [True]):
            value, _, _ = packet()
            value["approved_leafs"] = leafs
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)
        for leaf in ("browser_intents", "invented"):
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(required_leaf=leaf)

    def test_each_converter_leaf_can_be_approved_independently(self):
        for leaf in checkpoint.LEAF_NAMES:
            value, digests, _ = packet()
            value["approved_leafs"] = [leaf]
            value["review"]["converter_owners"] = {leaf: "synthetic-converter"}
            sources = {leaf: "1" * 64}
            value["bindings"]["leaf_source_sha256"] = sources.copy()
            self.assertEqual(self.validate(value, digests, sources, required_leaf=leaf)["approved_leafs"], [leaf])

    def test_multiple_approved_leafs_retain_distinct_ownership(self):
        value, digests, leaf_digests = packet()
        value["approved_leafs"].append("browser_intents")
        value["bindings"]["leaf_source_sha256"]["browser_intents"] = "2" * 64
        leaf_digests["browser_intents"] = "2" * 64
        owners = value["review"]["converter_owners"]
        owners["browser_intents"] = "another-converter"
        self.assertEqual(self.validate(value, digests, leaf_digests)["approved_leafs"],
                         ["browser_intents", "content_identity"])
        owners["browser_intents"] = owners["content_identity"]
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(value, digests, leaf_digests)

    def test_golden_reviewer_must_be_independent_of_all_preparers(self):
        for preparer in ("mapping_owner", "golden_extractor", "converter"):
            value, _, _ = packet()
            owner = (value["review"]["converter_owners"]["content_identity"]
                     if preparer == "converter" else value["review"][preparer])
            value["review"]["independent_approver"] = owner
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)

    def test_golden_extractor_cannot_be_its_converter_owner(self):
        value, _, _ = packet()
        value["review"]["golden_extractor"] = "synthetic-converter"
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(value)

    def test_empty_or_coerced_reviewer_identities_are_invalid(self):
        for identity in ("", " whitespace", "trailing ", None, True, 1):
            value, _, _ = packet()
            value["review"]["independent_approver"] = identity
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)

    def test_schema_unknown_fields_and_bool_version_are_invalid(self):
        for field in ("checkpoint", "bindings", "review"):
            value, _, _ = packet()
            target = value if field == "checkpoint" else value[field]
            target["unexpected"] = None
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)
        for version in (True, 1.0, "1", 2):
            value, _, _ = packet()
            value["schema_version"] = version
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(value)

    def test_semantic_golden_differences_or_missing_groups_block(self):
        after = snapshot()
        after["entities"]["reviews"] = [{"key": "synthetic-only", "data": {}}]
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(after=after)
        del after["entities"]["browser_intents"]
        with self.assertRaises(checkpoint.CheckpointError):
            self.validate(after=after)

    def test_golden_comparison_preserves_non_json_and_unknown_state_rejection(self):
        for value in (float("nan"), {1: "bad-key"}, (1, 2)):
            before = snapshot()
            before["entities"]["cards"] = [{"key": "card", "data": {"value": value}}]
            with self.assertRaises(checkpoint.CheckpointError):
                self.validate(before=before, after=before)

    def test_json_reader_rejects_duplicate_members_nonfinite_overflow_and_bad_utf8(self):
        for payload in (b'{"x": 1, "x": 2}', b'{"x": NaN}', b'{"x": 1e999}',
                        b'{} trailing', b'\xff'):
            with self.assertRaises(checkpoint.CheckpointError):
                checkpoint._decode_json(payload)

    def run_cli(self, directory, kind="synthetic"):
        value, _, _ = packet(kind)
        arguments = [str(directory / "checkpoint.json")]
        for name in checkpoint.ARTIFACT_NAMES:
            path = directory / f"{name}.json"
            content = json.dumps(snapshot()).encode() if name.startswith("golden_") and name != "golden_extraction" else name.encode()
            path.write_bytes(content)
            value["bindings"][f"{name}_sha256"] = hashlib.sha256(content).hexdigest()
            arguments.extend(["--artifact", name, str(path)])
        source_path = directory / "source.json"
        source_path.write_text('{"synthetic":"é", "approved_row_keys": []}')
        source_bytes = json.dumps(json.loads(source_path.read_text()), sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=False,
                                 allow_nan=False).encode("utf-8")
        value["bindings"]["leaf_source_sha256"]["content_identity"] = hashlib.sha256(source_bytes).hexdigest()
        arguments.extend(["--leaf-source", "content_identity", str(source_path)])
        (directory / "checkpoint.json").write_text(json.dumps(value))
        return arguments

    def call_cli(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", errors):
            code = checkpoint.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def test_cli_hashes_exact_files_and_canonical_source_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            arguments = self.run_cli(directory)
            originals = {path: path.read_bytes() for path in directory.iterdir()}
            code, output, errors = self.call_cli(arguments)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output)["consistent"])
            self.assertEqual(errors, "")
            self.assertNotIn(str(directory), output)
            self.assertNotIn("synthetic-reviewer", output)
            self.assertEqual(originals, {path: path.read_bytes() for path in directory.iterdir()})

    def test_cli_stale_snapshot_and_changed_approved_row_key_fail_closed_without_leaks(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-secret-path-") as directory:
            directory = Path(directory)
            for name in ("authorized_snapshot", "source"):
                arguments = self.run_cli(directory)
                path = directory / f"{name}.json"
                path.write_text('{"private-changed-row-key":"secret-value"}')
                code, output, errors = self.call_cli(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertNotIn("secret", errors)
                self.assertNotIn(str(directory), errors)

    def test_cli_synthetic_actual_and_missing_duplicate_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.run_cli(Path(directory))
            self.assertEqual(self.call_cli(arguments + ["--require-actual"])[0], 2)
            self.assertEqual(self.call_cli(arguments[:-3])[0], 2)
            self.assertEqual(self.call_cli(arguments + ["--artifact", "mapping", "unused"])[0], 2)
            self.assertEqual(self.call_cli(arguments + ["--leaf-source", "content_identity", "unused"])[0], 2)


if __name__ == "__main__":
    unittest.main()
