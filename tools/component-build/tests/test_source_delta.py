import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "source_delta.py"


class SourceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="source-delta-")
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Source Delta Fixture")
        self.classification = Path(self.temporary.name) / "classification.json"

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.decode().strip()

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def manifest(self, entries, raw=None):
        if raw is None:
            raw = json.dumps({"schema_version": 1, "paths": entries}, separators=(",", ":")).encode()
        self.classification.write_bytes(raw)
        return raw

    def run_tool(self, base, head):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--base",
                base,
                "--head",
                head,
                "--classification",
                str(self.classification),
            ],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def report(self, base, head):
        result = self.run_tool(base, head)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout)

    def test_add_delete_and_category_totals(self):
        self.write("services/remove.py", b"old\nline\n")
        self.write("packages/keep.py", b"one\n")
        base = self.commit("base")
        (self.root / "services/remove.py").unlink()
        self.write("services/add.py", b"one\ntwo")
        head = self.commit("add and delete")
        self.manifest(
            [
                {"path": "services/remove.py", "category": "narrow-production"},
                {"path": "services/add.py", "category": "broad-production"},
            ]
        )
        report = self.report(base, head)
        self.assertEqual(report["totals"]["narrow-production"], {"before": 2, "after": 0, "net": -2})
        self.assertEqual(report["totals"]["broad-production"], {"before": 2, "after": 2, "net": 0})
        self.assertEqual([row["path"] for row in report["changed_paths"]], ["services/add.py", "services/remove.py"])

    def test_pure_rename_includes_old_and_new_names(self):
        self.write("backend/old.py", b"a\nb\n")
        base = self.commit("base")
        (self.root / "backend/old.py").rename(self.root / "backend/new.py")
        head = self.commit("rename")
        self.manifest(
            [
                {"path": "backend/old.py", "category": "broad-production"},
                {"path": "backend/new.py", "category": "broad-production"},
            ]
        )
        report = self.report(base, head)
        self.assertEqual(report["totals"]["broad-production"], {"before": 2, "after": 2, "net": 0})
        self.assertEqual({row["path"] for row in report["changed_paths"]}, {"backend/old.py", "backend/new.py"})
        self.assertTrue(all(row["status"].startswith("R") for row in report["changed_paths"]))

    def test_cross_root_move_has_zero_broader_production_net(self):
        self.write("packages/source.py", b"alpha\nbeta\n")
        base = self.commit("base")
        (self.root / "services").mkdir()
        (self.root / "packages/source.py").rename(self.root / "services/source.py")
        head = self.commit("cross-root move")
        self.manifest(
            [
                {"path": "packages/source.py", "category": "narrow-production"},
                {"path": "services/source.py", "category": "broad-production"},
            ]
        )
        report = self.report(base, head)
        self.assertEqual(report["totals"]["broad-production"]["net"], 0)

    def test_test_only_change_does_not_reduce_production(self):
        self.write("backend/core.py", b"code\n")
        self.write("tools/example_test.py", b"old\n")
        base = self.commit("base")
        self.write("tools/example_test.py", b"new\nextra\n")
        head = self.commit("test update")
        self.manifest([{"path": "tools/example_test.py", "category": "test"}])
        report = self.report(base, head)
        self.assertEqual(report["totals"]["test"], {"before": 1, "after": 2, "net": 1})
        self.assertEqual(report["totals"]["broad-production"]["net"], 0)

    def test_missing_classification_fails_for_changed_protected_path(self):
        self.write("database/changed.sql", b"x\n")
        base = self.commit("base")
        self.write("database/changed.sql", b"x\ny\n")
        head = self.commit("change")
        self.manifest([])
        result = self.run_tool(base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"missing classifications", result.stderr)

    def test_duplicate_classifications_fail(self):
        self.write("services/item.py", b"x\n")
        base = self.commit("base")
        head = self.commit("head")
        self.manifest(
            [
                {"path": "services/item.py", "category": "other"},
                {"path": "services/item.py", "category": "other"},
            ]
        )
        result = self.run_tool(base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"duplicate classification", result.stderr)

    def test_missing_git_object_fails_clearly(self):
        self.manifest([])
        result = self.run_tool("deadbeef", "also-missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"invalid or missing base commit", result.stderr)

    def test_classified_path_must_exist_in_at_least_one_commit(self):
        self.write("backend/known.py", b"x\n")
        base = self.commit("base")
        head = self.commit("head")
        self.manifest([{"path": "backend/absent.py", "category": "other"}])
        result = self.run_tool(base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"absent from both commits", result.stderr)

    def test_empty_and_non_newline_terminated_files(self):
        self.write("contracts/empty.py", b"")
        self.write("contracts/unterminated.py", b"abc")
        base = self.commit("base")
        self.write("contracts/empty.py", b"x")
        self.write("contracts/unterminated.py", b"abc\n")
        head = self.commit("contents")
        self.manifest(
            [
                {"path": "contracts/empty.py", "category": "narrow-production"},
                {"path": "contracts/unterminated.py", "category": "narrow-production"},
            ]
        )
        report = self.report(base, head)
        self.assertEqual(report["totals"]["narrow-production"], {"before": 1, "after": 2, "net": 1})
        self.assertIn("LF bytes plus one", report["line_count_rule"])

    def test_classification_hash_and_output_are_deterministic(self):
        self.write("workers/worker.py", b"x\n")
        base = self.commit("base")
        self.write("workers/worker.py", b"x\ny\n")
        head = self.commit("change")
        raw = self.manifest([{"path": "workers/worker.py", "category": "broad-production"}])
        first = self.run_tool(base, head)
        second = self.run_tool(base, head)
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["classification_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["base_commit"], base)
        self.assertEqual(report["head_commit"], head)
        self.assertIn("explicitly classified", report["scope"])


if __name__ == "__main__":
    unittest.main()
