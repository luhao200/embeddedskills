<div align="center">

[简体中文](./README.md) | English

# embeddedskills — Embedded Development & Debugging Skills

**Give AI coding assistants direct control over compilers, debuggers, and communication buses — completing the full loop from code generation to hardware verification.**

[![MIT License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/status-active-success?style=flat-square)]()
[![Stars](https://img.shields.io/github/stars/zhinkgit/embeddedskills?style=flat-square)](https://github.com/zhinkgit/embeddedskills/stargazers)

[![Claude Code](https://img.shields.io/badge/Claude_Code-black?style=flat-square&logo=anthropic&logoColor=white)](https://claude.ai/code)
[![OpenAI Codex CLI](https://img.shields.io/badge/OpenAI_Codex_CLI-412991?style=flat-square&logo=openai&logoColor=white)](https://github.com/openai/codex)
[![TRAE](https://img.shields.io/badge/TRAE-0078D4?style=flat-square)](https://trae.ai)
[![Cursor](https://img.shields.io/badge/Cursor-000?style=flat-square&logo=cursor&logoColor=white)](https://cursor.sh)
[![Kiro](https://img.shields.io/badge/Kiro-232F3E?style=flat-square&logo=amazon&logoColor=white)](https://kiro.ai)

</div>

---

## ✨ Three Core Advantages

### 🔁 Embedded AI Workflow Closed Loop

Embedded development has a gap that pure software development doesn't have: writing code is just the beginning — compiling, flashing, and debugging all require human intervention at every step.

> AI modifies code → **You** manually compile → **You** manually flash → **You** copy errors to AI → AI fixes → **You** compile again...

**embeddedskills hands this loop over to AI:**

```mermaid
flowchart TD
    A["💬 Requirements Discussion"] --> B["✍️ Code Generation & Optimization"]
    B --> C["🔨 Auto Compile keil build / gcc build"]
    C -->|Errors| D["🤖 AI Reads Compiler Errors"]
    D --> B
    C -->|Success| E["⚡ Auto Flash openocd flash / jlink flash / probe-rs flash"]
    E --> F["🔬 Auto Debug & Verify"]
    F -->|Anomaly| G["🤖 AI Reads Debug Info"]
    G --> B
    F -->|Normal| H["✅ Submit Final Code"]

    style A fill:#4CAF50,color:#fff
    style H fill:#4CAF50,color:#fff
    style C fill:#2196F3,color:#fff
    style E fill:#2196F3,color:#fff
    style F fill:#FF9800,color:#fff
    style D fill:#f44336,color:#fff
    style G fill:#f44336,color:#fff
```

| Stage | Traditional AI Assistance | AI + embeddedskills |
|------|------------|-------------------|
| Code Writing | AI generates | AI generates |
| Compile/Build | **Manual** | ✅ AI invokes Keil / GCC |
| Flash/Download | **Manual** | ✅ AI invokes J-Link / OpenOCD |
| Debug/Verify | **Manual** | ✅ AI breakpoints / registers / memory |
| Communication Debug | **Manual** | ✅ AI serial / CAN / network |
| Error Fixing | **Manually paste errors to AI** | ✅ AI reads and fixes autonomously |

---

### 🆓 Completely Free, No AI Tool Lock-in

This project is fully open source and free (MIT license). Any AI tool that supports the Skill / CLAUDE.md / Rules protocol can use it directly, including but not limited to:

- **Claude Code**
- **OpenAI Codex CLI**
- **TRAE**
- **Cursor, Kiro, Windsurf** and other tools supporting the Skill protocol

No paid subscriptions required for any additional services. Switch AI tools freely.

---

### ⚡ Simple to Use, No Project Migration Required

**Works directly on your existing projects — no changes to any project files.** Supports mainstream build systems and debuggers in the industry:

| Dimension | Supported Range |
|------|-------|
| **Build Systems** | Keil MDK projects, CMake projects |
| **Debuggers** | J-Link (SEGGER), CMSIS-DAP / DAPLink |
| **Flash Frameworks** | OpenOCD, probe-rs compatible toolchain |
| **Communication Buses** | Serial (UART), CAN / CAN-FD, Ethernet |

One-command install, AI automatically detects project type and starts working:

```bash
npx skills add https://github.com/zhinkgit/embeddedskills -g -y
```

---

## Skill Overview

| Category | Skill | What It Does | Main Subcommands |
|:---:|:---:|---|---|
| 🔨 Build | **keil** | Keil MDK project scan, Target enumeration, compile, rebuild, clean | `scan` `targets` `build` `rebuild` `clean` `flash` |
| 🔨 Build | **gcc** | CMake-based GCC project configuration, compile, size analysis | `scan` `presets` `configure` `build` `rebuild` `size` |
| 🔬 Debug | **jlink** | Flash, read/write memory/registers, RTT/SWO, GDB debug | `flash` `read-mem` `write-mem` `regs` `rtt` `swo` + GDB |
| 🔬 Debug | **openocd** | Flash, erase, GDB/Telnet, Semihosting/ITM | `flash` `erase` `reset` `gdb-server` `semihosting` `itm` |
| 🔬 Debug | **probe-rs** | Probe discovery, flashing, reset, memory access, GDB debug, RTT | `list` `info` `flash` `erase` `reset` `read-mem` `write-mem` `gdb` `rtt` |
| 🔌 Communication | **serial** | Scan serial ports, real-time monitor, send data, hex view | `scan` `monitor` `send` `hex` `log` |
| 🔌 Communication | **can** | CAN/CAN-FD monitoring, send frames, DBC decode, statistics | `scan` `monitor` `send` `decode` `stats` |
| 🔌 Communication | **net** | Packet capture analysis, connectivity test, port scan, traffic stats | `capture` `analyze` `ping` `scan` `stats` |
| 🎯 Orchestration | **workflow** | Auto-detect project → Select toolchain → Orchestrate full flow | `plan` `build` `build-flash` `build-debug` `observe` `diagnose` |

> [!TIP]
> `Keil / GCC` and `J-Link / OpenOCD / probe-rs` can be freely combined orthogonally — all six combinations work out of the box.

---

## Installation

### Method 1: npx (Recommended)

```bash
# One-click install all skills
npx skills add https://github.com/zhinkgit/embeddedskills -g -y

# Install only specific skill (e.g., only openocd)
npx skills add https://github.com/zhinkgit/embeddedskills --skill openocd -g -y

# Management
npx skills ls -g        # List installed
npx skills update -g    # Update to latest
npx skills remove -g    # Remove
```

### Method 2: Direct Clone

```bash
# Claude Code (global)
git clone https://github.com/zhinkgit/embeddedskills.git ~/.claude/skills/embeddedskills

# Codex (global)
git clone https://github.com/zhinkgit/embeddedskills.git ~/.codex/skills

# Codex (current project only)
git clone https://github.com/zhinkgit/embeddedskills.git .codex/skills
```

> [!NOTE]
> **[→ Full Installation & Usage Guide](docs/getting-started.en.md)** — includes screenshot demonstrations and configuration instructions.

---

## How It Works

Three key designs enable true autonomous AI closed-loop:

<details>
<summary><b>① Wrap CLI Tools</b></summary>

Each Skill is a set of Python scripts that convert underlying tools (UV4.exe, cmake, JLink.exe, openocd, probe-rs, tshark, etc.) CLI parameters and interactive flows into structured subcommands, allowing AI to call these tools like functions.

</details>

<details>
<summary><b>② Expose to AI via SKILL.md</b></summary>

Each Skill directory contains a `SKILL.md` that describes capabilities, subcommands, and usage scenarios in natural language. After reading it, AI can invoke correctly — **no additional training or configuration needed**, any AI tool supporting the Skill protocol works out of the box.

</details>

<details>
<summary><b>③ Unified JSON Output Drives Next Decisions</b></summary>

All scripts return a unified JSON structure that AI parses directly for status, summary, and recommendations to autonomously decide next actions:

```json
{
  "status": "ok | error",
  "action": "build",
  "summary": "Build successful, 0 errors, 2 warnings",
  "details": { "warnings": ["unused variable 'x' at main.c:42"] },
  "artifacts": { "hex": ".embeddedskills/build/output.hex" },
  "next_actions": ["flash to device"]
}
```

</details>

<br>

**Three-layer configuration**, override as needed, priority from high to low:

```
CLI args  ──►  skill/config.json (tool paths, hardware params)
          ──►  .embeddedskills/config.json (target chip, interface, log dirs)
          ──►  .embeddedskills/state.json (last build/flash/debug record)
          ──►  Defaults
```

**Unified Log Directories:**

```
workspace/
└── .embeddedskills/
    ├── build/          ← Build logs and hex/bin artifacts
    └── logs/
        ├── serial/     ← Serial monitor logs
        ├── can/        ← CAN message logs
        └── net/        ← Network capture files
```

---

## External Dependencies

<details>
<summary>Expand to view dependencies for each Skill</summary>

| Skill | Dependencies |
|---|---|
| keil | Keil MDK (UV4.exe) |
| gcc | CMake · Ninja/Make · ARM GNU Toolchain |
| jlink | SEGGER J-Link Software · arm-none-eabi-gdb |
| openocd | OpenOCD · Debugger drivers (ST-Link / CMSIS-DAP / DAPLink / FTDI) |
| probe-rs | probe-rs CLI · arm-none-eabi-gdb |
| serial | pyserial · USB-to-serial driver |
| can | python-can · cantools · pyserial · USB-CAN driver |
| net | Wireshark (tshark) · Npcap |

> Except for CAN and serial, all Skills are implemented using Python standard library — no additional Python dependencies needed.

> [!WARNING]
> On Windows, using `probe-rs` with `J-Link` typically requires switching the probe driver to `WinUSB`, which can break the official SEGGER tooling. If you still rely on the SEGGER toolchain, prefer the existing `jlink` skill.

</details>

---

## Progress

| Skill | Status |
|---|:---:|
| keil | ✅ Tested |
| gcc | ✅ Tested |
| platformio | 🔧 To be supported |
| jlink | ✅ Tested |
| openocd | ✅ Tested |
| probe-rs | 🔧 Pending |
| pyocd | 🔧 To be supported |
| serial | ✅ Tested |
| net | ✅ Tested |
| can | 🔧 Pending test |
| workflow | ✅ Tested |

---

## Star History

<a href="https://www.star-history.com/?repos=zhinkgit%2Fembeddedskills&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&legend=top-left" />
 </picture>
</a>

Welcome to submit Issues and PRs. Thanks to the [Linux.do](https://linux.do/) community for their support.
