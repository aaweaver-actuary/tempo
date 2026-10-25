"""Synthetic contract tests; no installed data or authenticated approval is used."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / "tooling/history_authority.py"
SPEC = importlib.util.spec_from_file_location("history_authority", PATH)
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


def source():
    return {"schema_version": 1, "authority": {"fingerprint_sha256": "a" * 64},
            "tables": {"synthetic_table": [{"key": "opaque-physical-id", "row": {
                "answer": "e4", "first_issued_at": None, "events": [1, 2],
            }}]}, "unresolved": []}


def checkpoint(packet):
    # These labels are fabricated tests, never an authenticated actual receipt.
    return {"consistent": True, "evidence_kind": "actual", "actual_release": True,
            "authenticated": False, "final_acceptance": "unverified",
            "authority_fingerprint_sha256": "a" * 64,
            "checkpoint_sha256": "d" * 64,
            "approved_leafs": ["server_receipts"],
            "leaf_source_sha256": {"server_receipts": authority.canonical_packet_sha256(packet)}}


class HistoryAuthorityTests(unittest.TestCase):
    def test_canonical_hash_preserves_unicode_arrays_and_json_types(self):
        packet = {"z": [1, True, None], "é": "♞"}
        expected = hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(authority.canonical_packet_sha256(packet), expected)
        self.assertEqual(authority.canonical_packet_sha256(dict(reversed(list(packet.items())))), expected)
        self.assertNotEqual(authority.canonical_packet_sha256({"z": [True, 1, None], "é": "♞"}), expected)

    def test_exact_packet_requires_independent_approval_and_is_not_mutated(self):
        packet = source()
        receipt = checkpoint(packet)
        original = copy.deepcopy((packet, receipt))
        seen = []
        approve = authority.approved_source_validator(receipt, "server_receipts",
            independent_approval=lambda value: seen.append(value) or True)
        self.assertIs(approve(packet), True)
        self.assertEqual(seen, [receipt])
        self.assertEqual((packet, receipt), original)

    def test_mutating_receipt_or_callback_argument_cannot_change_frozen_binding(self):
        packet = source()
        receipt = checkpoint(packet)
        def approval(value):
            value["leaf_source_sha256"]["server_receipts"] = "b" * 64
            return True
        approve = authority.approved_source_validator(receipt, "server_receipts", independent_approval=approval)
        receipt["leaf_source_sha256"]["server_receipts"] = "c" * 64
        self.assertIs(approve(packet), True)

    def test_changed_key_answer_unknown_provenance_or_order_is_rejected(self):
        packet = source()
        approve = authority.approved_source_validator(checkpoint(packet), "server_receipts",
            independent_approval=lambda _: True)
        variants = []
        for field, value in (("answer", "d4"), ("first_issued_at", "2030-01-01"), ("events", [2, 1])):
            changed = copy.deepcopy(packet)
            changed["tables"]["synthetic_table"][0]["row"][field] = value
            variants.append(changed)
        changed = copy.deepcopy(packet)
        changed["tables"]["synthetic_table"][0]["key"] = "replacement-id"
        variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(authority.AuthorityError):
                approve(changed)

    def test_self_labeled_actual_or_synthetic_receipt_never_bypasses_trust(self):
        packet = source()
        for independent in (None, True, lambda _: 1, lambda _: False):
            with self.subTest(independent=independent), self.assertRaises(authority.AuthorityError):
                authority.approved_source_validator(checkpoint(packet), "server_receipts", independent_approval=independent)
        for field, value in (("actual_release", False), ("evidence_kind", "synthetic"),
                             ("consistent", False), ("authenticated", True), ("final_acceptance", True)):
            receipt = checkpoint(packet)
            receipt[field] = value
            with self.subTest(field=field), self.assertRaises(authority.AuthorityError):
                authority.approved_source_validator(receipt, "server_receipts", independent_approval=lambda _: True)

    def test_independent_failure_is_redacted(self):
        def failed(_):
            raise RuntimeError("private credential")
        with self.assertRaises(authority.AuthorityError) as caught:
            authority.approved_source_validator(checkpoint(source()), "server_receipts", independent_approval=failed)
        self.assertNotIn("private", str(caught.exception))

    def test_leaf_scope_and_digest_shape_fail_closed(self):
        packet = source()
        variants = []
        for field, value in (("approved_leafs", ["server_receipts", "server_receipts"]),
                             ("leaf_source_sha256", {"server_receipts": "unknown"}),
                             ("checkpoint_sha256", None),
                             ("authority_fingerprint_sha256", None)):
            receipt = checkpoint(packet)
            receipt[field] = value
            variants.append(receipt)
        for receipt in variants:
            with self.assertRaises(authority.AuthorityError):
                authority.approved_source_validator(receipt, "server_receipts", independent_approval=lambda _: True)
        with self.assertRaises(authority.AuthorityError):
            authority.approved_source_validator(checkpoint(packet), "content_identity", independent_approval=lambda _: True)

    def test_unknown_authority_and_unresolved_facts_are_rejected(self):
        for mutate in (lambda p: p["authority"].update(fingerprint_sha256="b" * 64),
                       lambda p: p.update(unresolved=["unknown"]),
                       lambda p: p.update(schema_version=True)):
            packet = source()
            mutate(packet)
            approve = authority.approved_source_validator(checkpoint(packet), "server_receipts", independent_approval=lambda _: True)
            with self.assertRaises(authority.AuthorityError):
                approve(packet)

    def test_non_json_nonfinite_and_invalid_unicode_are_rejected(self):
        for value in ({1: "key"}, float("nan"), float("inf"), (1, 2), "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(authority.AuthorityError):
                authority.canonical_packet_sha256(value)


if __name__ == "__main__":
    unittest.main()
