# Workflow

`workflow` 是一个薄编排层，只负责：

- 发现当前 workspace 中的 Keil / GCC 工程
- 选择构建、烧录、调试、观测后端
- 串联 `.embeddedskills/state.json`
- 聚合底层脚本输出

当前 `observe` 阶段会返回 `jlink:rtt`、`jlink:swo`、`openocd:semihosting`、`openocd:itm` 这几类候选观测后端。

它不会重写底层构建器、烧录器或 GDB 解析逻辑。

## 命令

```bash
python workflow/scripts/workflow_plan.py --json
python workflow/scripts/workflow_run.py plan --json
python workflow/scripts/workflow_run.py build --json
python workflow/scripts/workflow_run.py build-flash --json
python workflow/scripts/workflow_run.py build-debug --json
python workflow/scripts/workflow_run.py observe --json
python workflow/scripts/workflow_run.py diagnose --json
```

## 配置

复制 `workflow/config.example.json` 为 `workflow/config.json` 后，可固定首选后端与关键参数。
