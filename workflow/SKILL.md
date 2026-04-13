---
name: workflow
description: >-
  embeddedskills 的薄编排层，用于在当前 workspace 中发现工程、选择 build/flash/debug/observe
  后端、串联 .embeddedskills/state.json，并聚合底层 skill 的结果。
  当用户提到“一键构建烧录”“自动诊断”“串起 build -> flash -> debug -> observe”
  或显式调用 /workflow 时触发。
argument-hint: "[plan|build|build-flash|build-debug|observe|diagnose] ..."
---

# Workflow 编排层

本 skill 不重复实现底层逻辑，只做发现、选择、串联和聚合。

`observe` 阶段当前会给出 `jlink:rtt`、`jlink:swo`、`openocd:semihosting`、`openocd:itm` 这几类候选后端。

## 命令

```bash
python <skill-dir>/scripts/workflow_plan.py --json
python <skill-dir>/scripts/workflow_run.py plan --json
python <skill-dir>/scripts/workflow_run.py build --json
python <skill-dir>/scripts/workflow_run.py build-flash --json
python <skill-dir>/scripts/workflow_run.py build-debug --json
python <skill-dir>/scripts/workflow_run.py observe --json
python <skill-dir>/scripts/workflow_run.py diagnose --json
```

## 规则

- 发现多个工程或多个候选后端时，只返回候选列表，不自动猜测
- 构建、烧录、调试、观测之间优先通过 `.embeddedskills/state.json` 串联
- `observe` 只生成推荐命令，不在 workflow 内直接长时间占用观测通道
- 失败时优先返回哪个阶段失败，以及底层脚本的结构化错误
