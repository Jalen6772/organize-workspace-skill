"""Validate the machine-readable execution log; this is not an organizer.

用法：python3 verify_log.py 执行日志.json [--plan 移动清单.json]
退出码 0 = 无 error 级问题；1 = 存在 error 或日志不可读。
日志格式见 references/layouts.md「移动清单与验收记录」。工具只校验格式与内部一致性，
不核对磁盘实际状态，也不能证明日志内容真实。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_plan import _norm, HEX64, LIMITS  # noqa: E402  复用路径归一化与常量

LOG_VERSION = 1
OPERATIONS = {"move", "mkdir", "edit", "link"}
STATUSES = {"done", "failed", "skipped", "deferred"}
NEEDS_SHA = {"move", "edit"}  # done 时必须带搬移/修改前内容指纹
LIMITS = ("仅校验日志格式与内部一致性，以及与清单的对应关系；"
          "不能证明磁盘实际状态与日志一致，也不能证明日志内容真实。")


def _issue(issues, severity, check, detail, index=None):
    issues.append({"severity": severity, "check": check, "detail": detail,
                   "entry": index})


def check_log(log, plan=None):
    """log 为解析后的日志 JSON；plan 为解析后的清单 JSON 或 None。返回 issues 列表。"""
    issues = []
    if not isinstance(log, dict):
        _issue(issues, "error", "log_invalid", "日志顶层必须是对象")
        return issues
    if log.get("version") != LOG_VERSION:
        _issue(issues, "error", "log_version",
               f"version 必须是 {LOG_VERSION}，当前为 {log.get('version')!r}")
    entries = log.get("entries")
    if not isinstance(entries, list):
        _issue(issues, "error", "log_invalid", "日志缺少 entries 数组")
        return issues
    if not str(log.get("batch", "")).strip():
        _issue(issues, "error", "batch_missing", "缺少 batch 批次标识")

    plan_keys = set()
    if isinstance(plan, dict) and isinstance(plan.get("operations"), list):
        for op in plan["operations"]:
            if not isinstance(op, dict):
                continue
            kind = str(op.get("operation_type", "")).strip().lower()
            s_parts, _, _ = _norm(str(op.get("source_relative_path", "")))
            t_parts, _, _ = _norm(str(op.get("target_relative_path", "")))
            plan_keys.add((kind, "/".join(s_parts), "/".join(t_parts)))

    seen_seq, done_targets = {}, {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            _issue(issues, "error", "entry_invalid", f"entries[{i}] 不是对象", i)
            continue
        seq = e.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            _issue(issues, "error", "seq_invalid", f"entries[{i}] 的 seq 必须是正整数", i)
        elif seq in seen_seq:
            _issue(issues, "error", "seq_duplicate",
                   f"entries[{i}] 的 seq {seq} 与 entries[{seen_seq[seq]}] 重复", i)
        else:
            seen_seq[seq] = i
        kind = e.get("operation")
        if kind not in OPERATIONS:
            _issue(issues, "error", "operation_invalid",
                   f"entries[{i}] 的 operation「{kind!r}」不在 "
                   f"{'、'.join(sorted(OPERATIONS))} 之内", i)
        status = e.get("status")
        if status not in STATUSES:
            _issue(issues, "error", "status_invalid",
                   f"entries[{i}] 的 status「{status!r}」不在 "
                   f"{'、'.join(sorted(STATUSES))} 之内", i)
            continue
        s_parts, _, _ = _norm(str(e.get("source_relative_path", "")))
        t_parts, _, _ = _norm(str(e.get("target_relative_path", "")))
        tgt_key = "/".join(t_parts)
        sha = e.get("sha256_before")
        if status == "done":
            if kind in NEEDS_SHA:
                if not (isinstance(sha, str) and HEX64.match(sha)):
                    _issue(issues, "error", "sha_missing_or_bad",
                           f"entries[{i}] 是 {kind} 的 done 项，sha256_before 必须是"
                           "搬移/修改前内容的 64 位十六进制 SHA-256", i)
            elif sha not in (None, ""):
                _issue(issues, "warning", "sha_unexpected",
                       f"entries[{i}] 的 {kind} 通常不带 sha256_before", i)
            if kind == "move":
                if not s_parts or not t_parts:
                    _issue(issues, "error", "move_paths_missing",
                           f"entries[{i}] 的 move 缺少来源或目标路径", i)
                elif s_parts == t_parts:
                    _issue(issues, "error", "move_no_op",
                           f"entries[{i}] 的 move 来源与目标相同", i)
            if kind in NEEDS_SHA or kind in ("mkdir", "link"):
                if not t_parts:
                    _issue(issues, "error", "target_missing",
                           f"entries[{i}] 的 done 项缺少目标路径", i)
                elif tgt_key in done_targets:
                    _issue(issues, "error", "target_done_twice",
                           f"entries[{i}] 与 entries[{done_targets[tgt_key]}] "
                           f"对同一目标「{tgt_key}」各有一个 done", i)
                else:
                    done_targets[tgt_key] = i
            if plan_keys and (kind, "/".join(s_parts), tgt_key) not in plan_keys:
                _issue(issues, "error", "not_in_plan",
                       f"entries[{i}] 的 done 项（{kind} {tgt_key}）"
                       "在移动清单中找不到对应操作", i)
        else:
            if sha is not None and not (isinstance(sha, str) and HEX64.match(sha)):
                _issue(issues, "error", "sha_bad",
                       f"entries[{i}] 的 sha256_before 不是 64 位十六进制", i)
            if not str(e.get("detail", "")).strip():
                _issue(issues, "warning", "detail_missing",
                       f"entries[{i}] 是 {status} 项，detail 应说明原因", i)
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_json")
    parser.add_argument("--plan", help="对应的移动清单；提供后核对 done 项与清单一致")
    args = parser.parse_args()
    try:
        log = json.loads(Path(args.log_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        result = {"log": args.log_json, "passed": False,
                  "issues": [{"severity": "error", "check": "log_unreadable",
                              "detail": f"日志无法读取或解析：{e}", "entry": None}],
                  "counts": {"entries": 0, "errors": 1, "warnings": 0},
                  "limits": LIMITS}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    plan = None
    if args.plan:
        try:
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(json.dumps({"passed": False, "issues": [
                {"severity": "error", "check": "plan_unreadable",
                 "detail": f"清单无法读取或解析：{e}", "entry": None}]},
                ensure_ascii=False, indent=2))
            raise SystemExit(1)
    issues = check_log(log, plan)
    errors = [x for x in issues if x["severity"] == "error"]
    result = {
        "log": args.log_json,
        "plan": args.plan,
        "passed": not errors,
        "issues": issues,
        "counts": {"entries": len(log.get("entries", [])) if isinstance(log, dict) else 0,
                   "errors": len(errors), "warnings": len(issues) - len(errors)},
        "limits": LIMITS,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
