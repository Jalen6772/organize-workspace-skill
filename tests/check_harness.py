"""Counterexamples for the evaluator. Only changes newly generated temporary data."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from fixture import create, snapshot
from verify_fixture import verify


class HarnessChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ow-harness-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "case"

    def make(self, case):
        with contextlib.redirect_stdout(io.StringIO()):
            create(self.base, case)

    def test_existing_destination_is_not_overwritten(self):
        self.make("resume")
        before = snapshot(self.base)
        with self.assertRaises(SystemExit):
            create(self.base, "resume")
        self.assertEqual(before, snapshot(self.base))

    def test_audit_detects_content_change(self):
        self.make("audit")
        self.assertTrue(verify(self.base)["passed"])
        (self.base / "workspace/98_收件箱/付款.csv").write_text("changed", encoding="utf-8")
        result = verify(self.base)
        self.assertFalse(next(c["passed"] for c in result["checks"]
                              if c["check"] == "audit_tree_and_bytes_unchanged"))

    def test_untouched_organize_is_not_a_success(self):
        self.make("organize")
        result = verify(self.base)
        self.assertFalse(result["passed"])
        self.assertTrue(any(not c["passed"] for c in result["checks"]
                            if c["check"].startswith("content_ownership:")))

    def test_missing_original_is_detected(self):
        self.make("resume")
        (self.base / "workspace/98_收件箱/台账.csv").unlink()
        result = verify(self.base)
        self.assertFalse(next(c["passed"] for c in result["checks"]
                              if c["check"] == "original_bytes_preserved:98_收件箱/台账.csv"))

    def test_external_change_is_detected(self):
        self.make("resume")
        (self.base / "outside/keep.txt").write_text("changed", encoding="utf-8")
        result = verify(self.base)
        self.assertFalse(next(c["passed"] for c in result["checks"] if c["check"] == "outside_unchanged"))

    def test_broken_current_link_is_detected(self):
        self.make("organize")
        (self.base / "workspace/00_入口.md").write_text("[broken](missing.md)\n[broken](missing2.md)", encoding="utf-8")
        result = verify(self.base)
        self.assertFalse(next(c["passed"] for c in result["checks"]
                              if c["check"] == "current_link_resolves:missing.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
