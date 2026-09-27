"""Read-only checks of a move plan before execution; this is not an organizer.

用法：python3 verify_plan.py 移动清单.json [--root 工作区根目录]
退出码 0 = 无 error 级问题；1 = 存在 error 或清单不可读。warning 不影响退出码。
无 --root 时只做字符串级静态检查；给出 --root 时额外核对真实文件系统。
工具只报告，绝不移动、修改或删除任何文件。
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path

REQUIRED_FIELDS = ("source_relative_path", "target_relative_path", "operation_type")
TEXT_FIELDS = ("reason", "evidence")
OPERATIONS = {"move", "mkdir", "edit", "link"}
CREATING = {"move", "mkdir", "link"}  # 会占用目标路径的操作；edit 修改既有文件
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..+)?$", re.IGNORECASE)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ABSOLUTE = re.compile(r"^([A-Za-z]:|\\\\|/|~)")
MAX_PATH_WINDOWS = 260
MAX_REL_HEURISTIC = 200
LIMITS = ("仅字符串与文件系统层核对；不判断归类是否正确，不能发现执行期间的内容变化，"
          "也不模拟同步客户端竞态。应在执行前用同一根目录运行。")


def _issue(issues, severity, check, detail, index=None):
    issues.append({"severity": severity, "check": check, "detail": detail,
                   "operation": index})


def _norm(rel):
    """计划内路径的统一预处理：反斜杠转斜杠、去 ./ 前缀；返回 (parts, 原文是否含反斜杠)。"""
    raw = rel.replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    return parts, raw, rel != raw


def _path_issues(issues, rel, field, index, parts, had_backslash):
    if ABSOLUTE.match(rel):
        _issue(issues, "error", "path_not_relative",
               f"{field}「{rel}」必须是工作区内相对路径（不允许绝对路径、盘符或 ~）", index)
    if rel.startswith("~"):
        _issue(issues, "error", "path_not_relative", f"{field}「{rel}」不允许 ~ 展开", index)
    if ".." in parts:
        _issue(issues, "error", "path_escape", f"{field}「{rel}」包含 ..，越出授权根目录", index)
    if "\x00" in rel:
        _issue(issues, "error", "path_invalid", f"{field}「{rel}」包含 NUL 字节", index)
    if had_backslash:
        _issue(issues, "warning", "path_backslash",
               f"{field}「{rel}」使用了反斜杠分隔符；清单应统一使用 /", index)
    name = parts[-1] if parts else rel
    if RESERVED.match(name):
        _issue(issues, "warning", "windows_reserved_name",
               f"{field}「{rel}」的名称在 Windows 上是保留名（{name}），同步或迁移时会失败", index)
    if len(rel) > MAX_REL_HEURISTIC:
        _issue(issues, "warning", "path_length",
               f"{field}「{rel}」长度 {len(rel)} 超过启发式阈值 {MAX_REL_HEURISTIC}，"
               f"加上根目录后可能超 Windows 260 字符上限", index)


def check_plan(plan, root=None):
    """plan 为解析后的 JSON 对象；root 为 Path 或 None。返回 issues 列表。"""
    issues = []
    operations = plan.get("operations") if isinstance(plan, dict) else None
    if not isinstance(operations, list) or not operations:
        _issue(issues, "error", "plan_invalid",
               "清单缺少非空的 operations 数组（顶层应为对象，operations 为操作列表）")
        return issues
    root_resolved = Path(root).resolve() if root else None

    seen = {}  # 归一化目标 → (原文本, 序号)
    sources = {}  # 归一化来源 → 序号（仅 move）
    targets_raw = []
    parsed = []

    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            _issue(issues, "error", "plan_invalid", f"第 {i} 项不是对象", i)
            continue
        missing = [f for f in REQUIRED_FIELDS if not str(op.get(f, "")).strip()]
        if missing:
            _issue(issues, "error", "field_missing",
                   f"第 {i} 项缺少必填字段：{', '.join(missing)}", i)
            continue
        for f in TEXT_FIELDS:
            if not str(op.get(f, "")).strip():
                _issue(issues, "warning", "evidence_missing",
                       f"第 {i} 项缺少 {f}，归类依据不可核对", i)
        kind = str(op["operation_type"]).strip().lower()
        if kind not in OPERATIONS:
            _issue(issues, "warning", "operation_unknown",
                   f"第 {i} 项 operation_type「{kind}」不在 move/mkdir/edit/link 之内，人工确认含义", i)
        src, tgt = str(op["source_relative_path"]), str(op["target_relative_path"])
        sparts, sraw, sb = _norm(src)
        tparts, traw, tb = _norm(tgt)
        _path_issues(issues, src, "source_relative_path", i, sparts, sb)
        _path_issues(issues, tgt, "target_relative_path", i, tparts, tb)
        if kind == "move" and sparts and tparts and sparts == tparts:
            _issue(issues, "warning", "no_op",
                   f"第 {i} 项来源与目标相同，是空操作", i)
        parsed.append({"i": i, "kind": kind, "src": src, "tgt": tgt,
                       "sparts": sparts, "tparts": tparts})
        targets_raw.append((traw, i, kind))

        if kind == "move":
            key = "/".join(sparts)
            if key in sources:
                _issue(issues, "error", "duplicate_source",
                       f"第 {sources[key]} 与第 {i} 项搬移同一来源「{src}」，"
                       "一个来源只能出现在一个 move 里", i)
            else:
                sources[key] = i

    # 目标碰撞：完全重复、大小写、Unicode 规范化
    folded, nfc = {}, {}
    for traw, i, kind in targets_raw:
        tparts, _, _ = _norm(traw)
        key = "/".join(tparts)
        if key in seen:
            _issue(issues, "error", "duplicate_target",
                   f"第 {seen[key]} 与第 {i} 项占用同一目标「{traw}」", i)
        else:
            seen[key] = i
        fkey = key.casefold()
        if fkey in folded and folded[fkey] != key:
            _issue(issues, "error", "case_collision",
                   f"第 {folded[fkey][1]} 与第 {i} 项目标仅大小写不同"
                   f"（「{folded[fkey][0]}」vs「{traw}」），在不区分大小写的文件系统上互相覆盖", i)
        elif fkey not in folded:
            folded[fkey] = (traw, i)
        nkey = unicodedata.normalize("NFC", key)
        if nkey in nfc and nfc[nkey] != key:
            _issue(issues, "error", "unicode_collision",
                   f"第 {nfc[nkey][1]} 与第 {i} 项目标 Unicode 规范化后相同"
                   f"（「{nfc[nkey][0]}」vs「{traw}」），不同平台呈现同名", i)
        elif nkey not in nfc:
            nfc[nkey] = (traw, i)

    # 父子交叉：目标落入某个 move 的来源目录内部（含自身）
    for a in parsed:
        if not a["tparts"]:
            continue
        for b in parsed:
            if a is b or b["kind"] != "move" or not b["sparts"]:
                continue
            if a["tparts"][:len(b["sparts"])] == b["sparts"]:
                _issue(issues, "error", "crossover_move",
                       f"第 {a['i']} 项目标「{a['tgt']}」位于第 {b['i']} 项搬移来源"
                       f"「{b['src']}」内部；目录搬移后该目标路径失效，应改写为搬移后的新路径", a["i"])
                break

    # 顺序依赖：目标当前被另一个 move 的来源占用
    for a in parsed:
        if a["kind"] != "move":
            continue
        akey = "/".join(a["tparts"])
        for b in parsed:
            if a is b or b["kind"] != "move":
                continue
            if "/".join(b["sparts"]) == akey:
                _issue(issues, "warning", "target_swap_dependency",
                       f"第 {a['i']} 项目标「{a['tgt']}」当前是第 {b['i']} 项要搬走的来源；"
                       "执行顺序影响结果，清单应写明批次顺序", a["i"])

    if not root_resolved:
        return issues

    # ---- 以下为文件系统核对（需要 --root）----
    existing_dirs = {}

    def siblings(parent_parts):
        key = tuple(parent_parts)
        if key not in existing_dirs:
            base = root_resolved.joinpath(*parent_parts) if parent_parts else root_resolved
            try:
                existing_dirs[key] = [p.name for p in base.iterdir()]
            except OSError:
                existing_dirs[key] = None
        return existing_dirs[key]

    for a in parsed:
        s_exists = (root_resolved / a["src"]).exists() if a["sparts"] else False
        t_exists = (root_resolved / a["tgt"]).exists() if a["tparts"] else False
        if a["kind"] in ("move", "edit") and not s_exists:
            _issue(issues, "error", "source_missing",
                   f"第 {a['i']} 项来源「{a['src']}」在根目录下不存在", a["i"])
        if a["kind"] in CREATING and t_exists:
            _issue(issues, "error", "target_exists",
                   f"第 {a['i']} 项目标「{a['tgt']}」已存在，执行会覆盖或冲突", a["i"])
        if a["kind"] == "edit" and s_exists and not t_exists and a["src"] != a["tgt"]:
            pass  # edit 的目标即其来源，缺少来源已单独报错
        if a["kind"] == "mkdir" and t_exists and not (root_resolved / a["tgt"]).is_dir():
            _issue(issues, "error", "target_exists",
                   f"第 {a['i']} 项目标「{a['tgt']}」已被同名文件占用，无法创建目录", a["i"])
        for rel, field in ((a["src"], "source_relative_path"),
                           (a["tgt"], "target_relative_path")):
            parts, _, _ = _norm(rel)
            if not parts:
                continue
            resolved = (root_resolved / rel).resolve()
            if not resolved.is_relative_to(root_resolved):
                _issue(issues, "error", "symlink_escape",
                       f"第 {a['i']} 项 {field}「{rel}」经符号链接解析后落在授权根目录之外", a["i"])
        if len(str(root_resolved / a["tgt"])) > MAX_PATH_WINDOWS:
            _issue(issues, "warning", "path_length",
                   f"第 {a['i']} 项目标绝对路径超过 Windows {MAX_PATH_WINDOWS} 字符上限", a["i"])
        sib = siblings(a["tparts"][:-1])
        if sib is not None and a["tparts"] and a["kind"] in CREATING and not t_exists:
            name = a["tparts"][-1]
            nfc_name = unicodedata.normalize("NFC", name)
            for other in sib:
                if other == name:
                    continue
                if other.casefold() == name.casefold():
                    _issue(issues, "error", "case_collision",
                           f"第 {a['i']} 项目标「{a['tgt']}」与现有条目「{other}」仅大小写不同，"
                           "在不区分大小写的文件系统上会合并或覆盖", a["i"])
                elif unicodedata.normalize("NFC", other) == nfc_name:
                    _issue(issues, "error", "unicode_collision",
                           f"第 {a['i']} 项目标「{a['tgt']}」与现有条目「{other}」Unicode 规范化后相同", a["i"])
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument("--root", help="工作区根目录；缺省时只做字符串级静态检查")
    args = parser.parse_args()
    plan_path = Path(args.plan_json)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        result = {"plan": str(plan_path), "root": args.root, "passed": False,
                  "issues": [{"severity": "error", "check": "plan_unreadable",
                              "detail": f"清单无法读取或解析：{e}", "operation": None}],
                  "counts": {"operations": 0, "errors": 1, "warnings": 0},
                  "limits": LIMITS}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    issues = check_plan(plan, Path(args.root) if args.root else None)
    errors = [x for x in issues if x["severity"] == "error"]
    result = {
        "plan": str(plan_path),
        "root": str(Path(args.root).resolve()) if args.root else None,
        "passed": not errors,
        "issues": issues,
        "counts": {"operations": len(plan.get("operations", [])) if isinstance(plan, dict) else 0,
                   "errors": len(errors),
                   "warnings": len(issues) - len(errors)},
        "limits": LIMITS,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
