# 更新记录

人为可读的逐版变更。版本号遵循语义化：技能判定逻辑与文件清单不变、仅文档或打包调整的为 patch 版本。

## 1.2.0 · 2026-09-24

- 新增随包只读工具 `tools/verify_plan.py`：执行前校验移动清单——重复来源/目标、绝对路径与 `..` 越界、目标已存在、父子交叉搬移、大小写与 Unicode 规范化碰撞、符号链接越出授权根目录、Windows 保留名与路径长度；问题分 error/warning，无 `--root` 时退化为纯静态检查。
- 新增随包只读工具 `tools/verify_log.py`：校验执行日志格式（version/batch/entries、seq 唯一、status 与 operation 取值、done 项 sha256_before、同一目标不得两个 done），可选 `--plan` 核对 done 项与清单一致。
- 执行日志定义机器可读 JSON 格式（references/layouts.md 新节）；SKILL.md 执行流程第 2、4 步接入两个工具：有 Python 3.9+ 时清单先校验再执行、日志先校验再恢复。
- 新增 `tests/test_verify_plan.py`：28 个用例覆盖上述检查的正反例与 CLI 错误路径；tests/RESULTS.md 记录实跑结果与未覆盖范围。
- README 增加 English summary。
- 分类规则、执行流程的判断标准与既有四个测试场景无变化；新工具只做核对，不改变「无自动搬移/回滚程序」的技能定位。

## 1.1.1 · 2026-09-24

- README 增加「来源与许可」维护者署名，版本行链接到本更新记录。
- 新增本更新记录；`references/`、`tests/`、`examples/` 与技能判定逻辑均无变化。
- `SKILL.md`、`references/reuse.md` 仅版本标识与日期更新。

## 1.1.0 · 2026-09-14

- GitHub 公开发布（pre-release），新增 `tests/` 四个虚构场景的独立行为测试与只读检查器、`tools/build_release.py` 可复现打包、`examples/` 示例移动清单。
- 1.1.0 的行为验证结论见 [tests/RESULTS.md](tests/RESULTS.md)；对 1.0.0 的正文细项调整未逐条留存，合并概述如上，不做虚构。

## 1.0.0 · 2026-09-14

- 初版：工作区目录规范与内容归位技能，经 Notion 页面分发。
