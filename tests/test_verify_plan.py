"""Unit tests for the shipped read-only helpers verify_plan and verify_log.

These test the tools' logic on synthetic data; they are not AI behavior tests.
Run: python3 tests/test_verify_plan.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "organize-workspace" / "tools"))

import verify_plan  # noqa: E402
import verify_log  # noqa: E402


def op(src, tgt, kind="move", **extra):
    base = {"source_relative_path": src, "target_relative_path": tgt,
            "operation_type": kind, "reason": "r", "evidence": "e",
            "status": "planned"}
    base.update(extra)
    return base


def errors(issues):
    return [x for x in issues if x["severity"] == "error"]


def warnings(issues):
    return [x for x in issues if x["severity"] == "warning"]


def has(issues, check):
    return any(x["check"] == check for x in issues)


class PlanStaticChecks(unittest.TestCase):
    """字符串级检查，无需 --root。"""

    def test_valid_plan_passes(self):
        plan = {"operations": [op("98_收件箱/a.md", "01_公司甲/账号/a.md")]}
        self.assertEqual(errors(verify_plan.check_plan(plan)), [])

    def test_missing_operations_is_error(self):
        for bad in ({}, {"operations": []}, [op("a", "b")]):
            self.assertTrue(has(verify_plan.check_plan(bad), "plan_invalid"))

    def test_unreadable_plan_reported_by_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(REPO / "organize-workspace" / "tools" / "verify_plan.py"),
                 str(bad)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            result = json.loads(proc.stdout)
            self.assertTrue(any(x["check"] == "plan_unreadable" for x in result["issues"]))

    def test_duplicate_source_error(self):
        plan = {"operations": [op("a.md", "x/a.md"), op("a.md", "y/a.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "duplicate_source"))

    def test_duplicate_target_error(self):
        plan = {"operations": [op("a.md", "d/x.md"), op("b.md", "d/x.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "duplicate_target"))

    def test_traversal_and_absolute_paths_error(self):
        plan = {"operations": [op("../outside/a.md", "in/a.md"),
                               op("/etc/passwd", "in/b.md"),
                               op("a.md", "C:\\temp\\b.md")]}
        issues = verify_plan.check_plan(plan)
        self.assertTrue(has(issues, "path_escape"))
        self.assertTrue(has(issues, "path_not_relative"))
        self.assertTrue(has(issues, "path_backslash"))

    def test_crossover_move_error(self):
        plan = {"operations": [op("公司甲", "归档/公司甲"),
                               op("公司甲/临时.md", "公司甲/整理.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "crossover_move"))

    def test_case_collision_error(self):
        plan = {"operations": [op("a.md", "d/Report.md"), op("b.md", "d/report.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "case_collision"))

    def test_unicode_collision_error(self):
        plan = {"operations": [op("a.md", "d/café.md"),
                               op("b.md", "d/cafe\u0301.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "unicode_collision"))

    def test_reserved_name_and_length_warnings(self):
        plan = {"operations": [op("a.md", "d/CON.md"),
                               op("b.md", "d/" + "长" * 210 + ".md")]}
        issues = verify_plan.check_plan(plan)
        self.assertTrue(has(issues, "windows_reserved_name"))
        self.assertTrue(has(issues, "path_length"))
        self.assertEqual(errors(issues), [])

    def test_no_op_and_missing_evidence_warnings(self):
        plan = {"operations": [op("same.md", "same.md", reason="", evidence="")]}
        issues = verify_plan.check_plan(plan)
        self.assertTrue(has(issues, "no_op"))
        self.assertTrue(has(issues, "evidence_missing"))

    def test_swap_dependency_warning(self):
        plan = {"operations": [op("a.md", "b.md"), op("b.md", "c.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan), "target_swap_dependency"))

    def test_example_plan_passes_static(self):
        example = json.loads(
            (REPO / "examples" / "move-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(errors(verify_plan.check_plan(example)), [])


class PlanFilesystemChecks(unittest.TestCase):
    """需要 --root 的文件系统核对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "98_收件箱").mkdir()
        (self.root / "98_收件箱" / "a.md").write_text("A", encoding="utf-8")
        (self.root / "98_收件箱" / "b.md").write_text("B", encoding="utf-8")

    def test_target_exists_error(self):
        plan = {"operations": [op("98_收件箱/a.md", "98_收件箱/b.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan, self.root), "target_exists"))

    def test_missing_source_error(self):
        plan = {"operations": [op("98_收件箱/ghost.md", "d/ghost.md")]}
        self.assertTrue(has(verify_plan.check_plan(plan, self.root), "source_missing"))

    def test_existing_dir_with_same_name_error_for_mkdir(self):
        plan = {"operations": [op("", "98_收件箱", kind="mkdir", source_relative_path="-")]}
        issues = verify_plan.check_plan(plan, self.root)
        self.assertTrue(has(issues, "target_exists"))

    def test_symlink_escape_error(self):
        outside = self.root.parent / ("ow-outside-" + self._tmp.name.split("/")[-1])
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("s", encoding="utf-8")
        link = self.root / "98_收件箱" / "jump"
        link.symlink_to(outside, target_is_directory=True)
        try:
            plan = {"operations": [op("98_收件箱/a.md", "98_收件箱/jump/a.md")]}
            self.assertTrue(has(verify_plan.check_plan(plan, self.root),
                                "symlink_escape"))
        finally:
            link.unlink()
            (outside / "secret.txt").unlink()
            outside.rmdir()

    def test_symlink_inside_root_is_allowed(self):
        (self.root / "98_收件箱" / "alias").symlink_to(self.root / "98_收件箱",
                                                       target_is_directory=True)
        plan = {"operations": [op("98_收件箱/a.md", "98_收件箱/alias/fresh.md")]}
        self.assertEqual(errors(verify_plan.check_plan(plan, self.root)), [])

    def test_case_collision_against_existing_entry(self):
        (self.root / "98_收件箱" / "Report.md").write_text("R", encoding="utf-8")
        plan = {"operations": [op("98_收件箱/a.md", "98_收件箱/report.md")]}
        issues = verify_plan.check_plan(plan, self.root)
        # 在大小写不敏感的文件系统（默认 APFS）上报告 target_exists；
        # 在大小写敏感的文件系统上报告与现有条目的 case_collision。两者都算拦截成功。
        self.assertTrue(has(issues, "case_collision") or has(issues, "target_exists"),
                        json.dumps(issues, ensure_ascii=False))


class LogChecks(unittest.TestCase):
    def log(self, *entries, batch="2026-09-24-01"):
        return {"version": 1, "batch": batch, "entries": list(entries)}

    def entry(self, seq, kind="move", src="a.md", tgt="d/a.md",
              status="done", sha="a" * 64, detail=""):
        return {"seq": seq, "operation": kind, "source_relative_path": src,
                "target_relative_path": tgt, "status": status,
                "sha256_before": sha, "detail": detail}

    def test_valid_log_passes(self):
        self.assertEqual(errors(verify_log.check_log(self.log(self.entry(1)))), [])

    def test_version_and_batch_enforced(self):
        issues = verify_log.check_log({"version": 2, "batch": "",
                                       "entries": [self.entry(1)]})
        self.assertTrue(has(issues, "log_version"))
        self.assertTrue(has(issues, "batch_missing"))

    def test_seq_rules(self):
        issues = verify_log.check_log(self.log(self.entry(1), self.entry(1),
                                               self.entry("x")))
        self.assertTrue(has(issues, "seq_duplicate"))
        self.assertTrue(has(issues, "seq_invalid"))

    def test_status_and_operation_enforced(self):
        log = self.log(self.entry(1, status="finished"),
                       self.entry(2, kind="delete"))
        issues = verify_log.check_log(log)
        self.assertTrue(has(issues, "status_invalid"))
        self.assertTrue(has(issues, "operation_invalid"))

    def test_done_requires_sha_for_move_and_edit(self):
        log = self.log(self.entry(1, sha=None),
                       self.entry(2, kind="edit", sha="xyz"))
        issues = verify_log.check_log(log)
        self.assertTrue(has(issues, "sha_missing_or_bad"))

    def test_target_done_twice_error(self):
        log = self.log(self.entry(1), self.entry(2, src="b.md"))
        self.assertTrue(has(verify_log.check_log(log), "target_done_twice"))

    def test_failed_entry_should_explain(self):
        log = self.log(self.entry(1, status="failed", sha=None, detail=""))
        issues = verify_log.check_log(log)
        self.assertTrue(has(issues, "detail_missing"))
        self.assertEqual(errors(issues), [])

    def test_not_in_plan_error(self):
        plan = {"operations": [op("a.md", "d/other.md")]}
        issues = verify_log.check_log(self.log(self.entry(1)), plan)
        self.assertTrue(has(issues, "not_in_plan"))

    def test_in_plan_passes(self):
        plan = {"operations": [op("a.md", "d/a.md")]}
        self.assertEqual(errors(verify_log.check_log(self.log(self.entry(1)), plan)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
