简体中文 | [English](./README.en.md)

# embeddedskills — 嵌入式开发调试 Skill 集

**让 AI 编码助手直接操控编译器、调试器和通信总线，补上嵌入式开发自动化的最后一环。**

适用于 Claude Code、Copilot、TRAE 及其他支持 Skill 协议的 AI 编码助手。

## 动机

AI 编码助手已能高效辅助方案设计和代码编写，但嵌入式开发不同于纯软件——写完代码只是开始，编译、烧录、调试仍需开发者手动完成。每次 AI 改完代码，你都要手动编译、烧录、观察结果、再把错误信息喂回给 AI，这个循环既低效又打断心流。

本工具集将嵌入式工具链的命令行能力封装为 Skill 接口，使 AI 能自主完成完整的 **编写 → 编译 → 烧录 → 调试 → 修正** 闭环：

```mermaid
flowchart TD
    A[AI 编写/修改代码] --> B[AI 调用 keil/gcc 编译]
    B --> C{编译通过？}
    C -->|有错误| D[AI 读取报错信息]
    D --> A
    C -->|通过| E[AI 调用 jlink/openocd 烧录]
    E --> F[AI 进行调试验证]
    F --> G{功能正常？}
    G -->|异常| H[AI 读取调试信息]
    H --> A
    G -->|正常| I[任务完成]

    style A fill:#4CAF50,color:#fff
    style B fill:#2196F3,color:#fff
    style E fill:#2196F3,color:#fff
    style F fill:#FF9800,color:#fff
    style I fill:#4CAF50,color:#fff
```

| 环节 | 传统 AI 辅助 | AI + Skills |
|------|-------------|-------------|
| 代码编写 | AI 生成 | AI 生成 |
| 编译构建 | 人工操作 | AI 调用 Keil / GCC |
| 烧录下载 | 人工操作 | AI 调用 J-Link / OpenOCD |
| 调试验证 | 人工操作 | AI 断点 / 寄存器 / 内存 |
| 通信调试 | 人工操作 | AI 串口 / CAN / 网络 |
| 错误修正 | 人工转述给 AI | AI 读取并自主修正 |

## 工作原理

- **封装命令行工具** — 每个 Skill 是一组 Python 脚本，将底层工具（UV4.exe、cmake、JLink.exe、openocd、tshark 等）的命令行参数和交互流程转化为结构化子命令
- **通过 SKILL.md 暴露给 AI** — 每个 Skill 目录下的 `SKILL.md` 以自然语言描述能力、子命令和使用场景，AI 读取后即可正确调用
- **统一 JSON 输出** — 所有脚本返回统一的 JSON 格式（状态、摘要、详情、产物路径、下一步建议等），AI 直接解析并决策下一步操作

## 使用方式：自动编排 + 手动组合

- **自动编排** — `workflow` 作为主 Skill，自动识别项目类型（Keil / GCC）、发现可用调试工具和通信接口，协调子 Skill 完成完整流程
- **手动调用** — 每个 Skill 可独立使用，按需执行特定任务（如 `jlink flash` 烧录、`serial monitor` 监控串口）

```
自动编排模式：                        手动调用模式：

workflow                              用户 / AI 直接调用
  ├─ 识别工程 → keil 或 gcc 编译        ├─ keil build
  ├─ 选择工具 → jlink 或 openocd 烧录   ├─ jlink flash
  ├─ 选择通道 → serial / can / net 观测  ├─ serial monitor
  └─ 聚合结果 → 决策下一步               └─ ...
```

## Skill 一览

| 分类 | Skill | 用途 | 子命令 |
|------|-------|------|--------|
| **构建** | **keil** | Keil MDK 工程扫描、Target 枚举、编译、重建、清理 | `scan` `targets` `build` `rebuild` `clean` `flash` |
| | **gcc** | CMake 型 GCC 嵌入式工程扫描、preset 枚举、配置、编译、大小分析 | `scan` `presets` `configure` `build` `rebuild` `clean` `size` |
| **调试** | **jlink** | J-Link 烧录、读写内存/寄存器、RTT/SWO、在线调试、GDB 调试 | `info` `flash` `read-mem` `write-mem` `regs` `reset` `rtt` `swo` `halt` `go` `step` `run-to` + GDB 子命令 |
| | **openocd** | OpenOCD 烧录、擦除、底层查询、GDB/Telnet 调试、Semihosting/ITM | `probe` `flash` `erase` `reset` `reset-init` `targets` `flash-banks` `adapter-info` `raw` `gdb-server` + GDB/Telnet 子命令 `semihosting` `itm` |
| **通信** | **serial** | 串口扫描、实时监控、数据发送、Hex 查看、日志 | `scan` `monitor` `send` `hex` `log` |
| | **can** | CAN/CAN-FD 接口扫描、监控、发帧、DBC 解码、统计 | `scan` `monitor` `send` `log` `decode` `stats` |
| | **net** | 抓包、pcap 分析、连通性测试、端口扫描、流量统计 | `iface` `capture` `analyze` `ping` `scan` `stats` |
| **编排** | **workflow** | 发现工程、选择后端、串联 workspace 状态、聚合结果 | `plan` `build` `build-flash` `build-debug` `observe` `diagnose` |

> 构建与调试正交组合：`Keil → J-Link`、`Keil → OpenOCD`、`GCC → J-Link`、`GCC → OpenOCD` 均可。`gcc` skill 当前面向 CMake 型 arm-none-eabi-gcc 工程，不含纯 Makefile 工程。

## 安装

### npx 安装（推荐）

```bash
# 安装全部 skill（全局）
npx skills add https://github.com/luhao200/embeddedskills -g -y

# 仅安装某个 skill
npx skills add https://github.com/luhao200/embeddedskills --skill jlink -g -y

# 管理
npx skills ls -g        # 查看已安装
npx skills update -g    # 更新
npx skills remove -g    # 移除
```

### 克隆到本地

```bash
# 全局生效
git clone https://github.com/luhao200/embeddedskills ~/.claude/skills/embeddedskills

# 仅当前项目
git clone https://github.com/luhao200/embeddedskills .claude/skills/embeddedskills
```

### 配置

将所需 skill 的 `config.example.json` 复制为 `config.json`，填入本地路径和参数：

```bash
cd ~/.claude/skills/embeddedskills/jlink
cp config.example.json config.json
# 编辑 config.json，填写 JLink.exe 路径、默认芯片型号等
```

> `config.json` 已被 `.gitignore` 排除，不会提交到仓库。

### 外部依赖

| Skill | 依赖 |
|-------|------|
| keil | Keil MDK (UV4.exe) |
| gcc | CMake, Ninja/Make, ARM GNU Toolchain |
| jlink | SEGGER J-Link Software, arm-none-eabi-gdb |
| openocd | OpenOCD, 调试器驱动 (ST-Link / CMSIS-DAP / DAPLink / FTDI) |
| serial | pyserial + USB 转串口驱动 |
| can | python-can, cantools, pyserial + USB-CAN 驱动 |
| net | Wireshark (tshark), Npcap |

## 架构

### Skill 目录结构

```
<skill>/
├── SKILL.md            # 元数据与执行规则（AI 读取此文件）
├── config.json         # 本地配置（.gitignore 已排除）
├── config.example.json # 配置模板
├── scripts/            # Python 脚本
└── references/         # 参考数据 (JSON/Markdown)
```

### 输出格式

所有脚本返回统一 JSON：

```json
{
  "status": "ok|error",
  "action": "...",
  "summary": "简短摘要",
  "details": {},
  "artifacts": {},
  "next_actions": []
}
```

完整字段还包括 `context`、`metrics`、`state`、`timing`。流式命令使用 JSON Lines。

### Workspace 状态

运行期状态存放在项目目录的 `.embeddedskills/state.json`，记录 `last_build`、`last_flash`、`last_debug`、`last_observe`。

### 执行模式

带执行风险的 skill 可通过 `config.json` 中的 `operation_mode` 控制（当前主要是 `keil`、`gcc`、`jlink`、`openocd`）：

| 模式 | 说明 |
|------|------|
| 1 | 立即执行 |
| 2 | 显示风险摘要，不阻断 |
| 3 | 执行前要求确认 |

### 设计原则

- 不猜测关键参数 — 设备型号、接口、端口等必须明确指定
- 多选项时列出候选 — 不自动选择
- 失败时提供排查建议
- 纯 Python 标准库实现（CAN 和串口除外）

## 完成进度

| Skill | 状态 |
|-------|------|
| keil | ✅ 已完成测试 |
| gcc | ✅ 已完成测试 |
| jlink | ✅ 已完成测试 |
| serial | ✅ 已完成测试 |
| net | ✅ 已完成测试 |
| openocd | ✅ 已完成测试 |
| workflow | 🔧 待测试 |
| can | 🔧 待测试 |

## License

MIT — 详见 [LICENSE](LICENSE)。
