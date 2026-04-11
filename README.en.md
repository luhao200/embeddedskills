[简体中文](./README.md) | English

# embeddedskills — Embedded Development & Debugging Toolkit

**Enable AI to do more than write code — let it compile, flash, and debug, completing the last mile of embedded development automation.**

This is an open-source collection of embedded development and debugging skills for Claude Code, Copilot, TRAE, and other AI coding assistants that support the Skill protocol. Once installed, the AI assistant can directly operate compilers, debuggers, and communication buses, automating the full workflow from code generation to hardware verification.

## Why is this toolkit needed?

### Current situation: AI only helps with half the job

Today's AI coding assistants (Claude, Copilot, TRAE, etc.) are already very effective for solution design and code writing. But embedded development is different from pure software development — writing code is only the beginning. Steps that interact with hardware, such as **compiling, flashing, and debugging**, still need to be done manually by developers.

```
In the traditional workflow, AI only covers the first half:

  What AI can do       ┃  What still needs humans
  ━━━━━━━━━━━━━━━━━━━━━╋━━━━━━━━━━━━━━━━━━━━━━━
  Solution design      ┃  Compile and build
  Code writing         ┃  Flash and download
  Code review          ┃  Breakpoint debugging
                       ┃  Serial/CAN/network debugging
                       ┃  Issue diagnosis and fixes
```

**Pain point:** Every time AI changes the code, you still need to compile manually, flash the firmware, observe the results, and feed the error information back to the AI. This loop is inefficient and constantly breaks flow.

### Solution: let AI close the loop on its own

This skill set gives AI assistants the ability to operate hardware toolchains, allowing them to complete the full development-debug cycle autonomously:

```mermaid
flowchart TD
    A[AI writes/modifies code] --> B[AI invokes Keil to compile]
    B --> C{Compile successful?}
    C -->|No| D[AI reads compiler errors]
    D --> A
    C -->|Yes| E[AI invokes jlink/openocd to flash]
    E --> F[AI performs debugging and verification]
    F --> G{Function works correctly?}
    G -->|No| H[AI reads debugging information]
    H --> A
    G -->|Yes| I[✅ Task completed]

    style A fill:#4CAF50,color:#fff
    style B fill:#2196F3,color:#fff
    style E fill:#2196F3,color:#fff
    style F fill:#FF9800,color:#fff
    style I fill:#4CAF50,color:#fff
```

**Full workflow AI can execute autonomously:**

1. **Write code** → Generate or modify source files based on requirements
2. **Compile and check** → Invoke Keil, read build errors, and automatically fix code until the build succeeds
3. **Flash firmware** → Download firmware to the chip through J-Link / OpenOCD
4. **Breakpoint debugging** → Set breakpoints, single-step, inspect registers and memory
5. **Communication debugging** → Read runtime data through serial / CAN / network and judge program behavior
6. **Self-correction** → Adjust code automatically according to debugging results and repeat the loop until the feature meets expectations

### Traditional development vs AI-empowered development

```mermaid
flowchart LR
    subgraph traditional["🔧 Traditional Development"]
        direction TB
        T1["AI writes code"] --> T2["Manual compile"]
        T2 --> T3["Manual flashing"]
        T3 --> T4["Manual debugging"]
        T4 --> T5["Manual feedback to AI"]
        T5 --> T1
    end

    subgraph empowered["🚀 AI + Skills"]
        direction TB
        E1["AI writes code"] --> E2["AI compiles autonomously"]
        E2 --> E3["AI flashes autonomously"]
        E3 --> E4["AI debugs autonomously"]
        E4 --> E5["AI judges results and fixes code"]
        E5 --> E1
    end

    traditional ~~~ empowered

    style traditional fill:#FFF3E0,stroke:#FF9800
    style empowered fill:#E8F5E9,stroke:#4CAF50
```

| Item | Traditional AI assistance | AI + Skills |
|------|---------------------------|-------------|
| Code writing | ✅ AI-generated | ✅ AI-generated |
| Compile/build | ❌ Manual operation | ✅ AI invokes Keil autonomously |
| Flash/download | ❌ Manual operation | ✅ AI invokes J-Link/OpenOCD autonomously |
| Debug/verification | ❌ Manual operation | ✅ AI handles breakpoints/registers/memory |
| Communication debugging | ❌ Manual operation | ✅ AI handles serial/CAN/network |
| Error fixing | ❌ Human relays information to AI | ✅ AI reads results and fixes code directly |
| **Development loop** | **❌ Human in the loop** | **✅ Fully autonomous AI loop** |

## Architecture Overview

```mermaid
graph TB
    AI["🤖 AI Coding Assistant<br/>(Claude Code / Copilot / TRAE)"]

    subgraph skills["embeddedskills Toolkit"]
        direction LR
        subgraph build["Build Layer"]
            keil["keil<br/>Keil MDK Build"]
        end
        subgraph flash["Flash Layer"]
            jlink["jlink<br/>J-Link Debug"]
            openocd["openocd<br/>OpenOCD Debug"]
        end
        subgraph comm["Communication Layer"]
            serial["serial<br/>Serial Debug"]
            can["can<br/>CAN Bus"]
            net["net<br/>Network Debug"]
        end
    end

    subgraph hw["Hardware Target"]
        MCU["🔌 MCU / SoC"]
    end

    AI --> skills
    build --> flash
    flash --> hw
    comm --> hw
    AI --> ext

    style AI fill:#9C27B0,color:#fff
    style skills fill:#F5F5F5,stroke:#999
    style build fill:#E3F2FD,stroke:#2196F3
    style flash fill:#FFF3E0,stroke:#FF9800
    style comm fill:#E8F5E9,stroke:#4CAF50
    style ext fill:#F3E5F5,stroke:#9C27B0
    style hw fill:#FFEBEE,stroke:#F44336
```

## Skill List

| Skill | Purpose | Subcommands |
|-------|---------|-------------|
| **keil** | Keil MDK project build, rebuild, clean, and flash | `scan` `targets` `build` `rebuild` `clean` `flash` |
| **jlink** | J-Link flashing, memory access, registers, RTT, and on-target debugging | `info` `flash` `read-mem` `write-mem` `regs` `reset` `rtt` `halt` `go` `step` `run-to` `gdb run` `gdb backtrace` `gdb locals` |
| **openocd** | OpenOCD flashing, erase, GDB server, and target reset | `probe` `flash` `erase` `gdb-server` `reset` |
| **serial** | Serial port scan, live monitor, data send, hex view, and logging | `scan` `monitor` `send` `hex` `log` |
| **can** | CAN/CAN-FD interface scan, monitoring, frame sending, and DBC decoding | `scan` `monitor` `send` `log` `decode` `stats` |
| **net** | Packet capture, pcap analysis, connectivity testing, and port scan | `iface` `capture` `analyze` `ping` `scan` `stats` |

## Installation

### Method 1: Install with npx (recommended)

```bash
# Install all skills globally
npx skills add https://github.com/luhao200/embeddedskills -g -y

# Install only one specific skill
npx skills add https://github.com/luhao200/embeddedskills --skill jlink -g -y
```

Common management commands:

```bash
npx skills ls -g        # List installed skills
npx skills update -g    # Update
npx skills remove -g    # Remove
```

### Method 2: Clone locally

```bash
# Clone the repository into the skill directory (global effect)
git clone https://github.com/luhao200/embeddedskills ~/.claude/skills/embeddedskills

# Or use it only for the current project (under the project root)
git clone https://github.com/luhao200/embeddedskills .claude/skills/embeddedskills
```

### Configuration

After installation, copy the `config.example.json` of the skill you want to use to `config.json`, then fill in the actual local paths and parameters:

```bash
cd ~/.claude/skills/embeddedskills/jlink
cp config.example.json config.json
# Edit config.json and fill in the JLink.exe path, default chip model, etc.
```

> `config.json` is excluded by `.gitignore` and will not be committed.

### Dependencies

| Skill | External dependencies |
|-------|------------------------|
| keil | Keil MDK (UV4.exe) |
| jlink | SEGGER J-Link Software, arm-none-eabi-gdb |
| openocd | OpenOCD, debugger drivers (ST-Link/CMSIS-DAP/DAPLink/FTDI) |
| serial | `pip install pyserial` + USB-to-serial driver |
| can | `pip install python-can cantools pyserial` + USB-CAN driver |
| net | Wireshark (tshark), Npcap |

## Detailed Skill Descriptions

### keil — Keil MDK Build

Scans `.uvprojx` / `.uvmpw` project files, enumerates Targets, performs incremental build / full rebuild / clean / flash, and parses build logs to extract error count, warning count, code size, and other information.

**Implementation:** Python scripts invoke the UV4.exe command line, then parse the return code and build logs. Flashing is only allowed when the build completes without errors.

---

### jlink — J-Link Probe Debugging

**Basic operations:** probe detection (`info`), firmware flashing (`flash`), memory read/write (`read-mem` / `write-mem`), register inspection (`regs`), target reset (`reset`), RTT logging (`rtt`)

**Lightweight debugging:** halt (`halt`) / resume (`go`) / single-step (`step`) / run to breakpoint (`run-to`)

**Source-level debugging through GDB:** arbitrary GDB commands (`gdb run`), backtrace (`gdb backtrace`), local variables (`gdb locals`)

**Implementation:**
- `jlink_exec.py` — generates a `.jlink` command script and executes it with JLink.exe
- `jlink_rtt.py` — starts JLinkGDBServerCL + JLinkRTTClient to read RTT output
- `jlink_gdb.py` — starts the GDB server and uses arm-none-eabi-gdb to run command sequences

---

### openocd — OpenOCD Debugging and Flashing

Probe detection (`probe`), firmware flashing (`flash`), flash erase (`erase`), GDB server startup (`gdb-server`), and target reset (`reset`).

**Implementation:** Python scripts assemble and execute OpenOCD command-line arguments. Board configuration takes priority over interface + target combinations. In GDB server mode, the process stays alive and returns port information.

**Supported debuggers:** ST-Link V2/V3, CMSIS-DAP, DAPLink, J-Link, FTDI

---

### serial — Serial Debugging

Scans available serial ports (`scan`), performs live text monitoring (`monitor`), sends text/hex data (`send`), displays binary hex output (`hex`), and records logs (`log`).

**Implementation:** Five standalone scripts based on pyserial. Streaming commands use JSON Lines output. Supports regex filtering and multiple log formats (text/csv/json). Includes built-in USB-to-serial VID/PID mappings for chips such as CH340, CP2102, FT232, and PL2303.

---

### can — CAN Bus Debugging

Interface scan (`scan`), live monitoring (`monitor`), frame sending (`send`), traffic logging (`log`), DBC/ARXML/KCD database decoding (`decode`), and bus statistics (`stats`).

**Implementation:** Six scripts based on python-can + cantools, supporting multiple backends including PCAN, Vector, IXXAT, Kvaser, slcan, socketcan, gs_usb, and virtual.

---

### net — Network Debugging

Interface discovery (`iface`), live packet capture (`capture`), offline pcap analysis (`analyze`), connectivity testing (`ping`), port scan (`scan`), and traffic statistics (`stats`).

**Implementation:** Six scripts based on tshark / capinfos. The port scan covers common embedded ports by default, including Modbus TCP, MQTT, CoAP, OPC UA, S7comm, BACnet, and EtherNet/IP.

---

## Common Architecture

### Directory Structure

Each skill uses the following directory structure:

```
<skill>/
├── SKILL.md            # Skill metadata and execution rules (required)
├── README.md           # User documentation
├── config.json         # Current configuration (excluded by .gitignore)
├── config.example.json # Configuration template
├── scripts/            # Python scripts
└── references/         # Reference data (JSON/Markdown)
```

### Unified Output Format

```json
{ "status": "ok|error", "action": "...", "summary": "...", "details": {...} }
```

Streaming commands use JSON Lines, and summary information is written to stderr.

### Operation Modes

Controlled by `operation_mode` in `config.json`:

| Mode | Description |
|------|-------------|
| 1 | Execute immediately |
| 2 | Show a risk summary without blocking |
| 3 | Require confirmation before execution |

### Design Principles

- **Do not guess critical parameters** — device model, interface, port, and similar inputs must be specified explicitly
- **List candidates when multiple options exist** — never auto-select
- **Provide troubleshooting suggestions on failure**
- **Implemented with the Python standard library only** (except CAN and serial, which require python-can / pyserial)

## Progress Status

| Skill | Status |
|-------|--------|
| keil | ✅ Tested and completed |
| jlink | ✅ Tested and completed |
| serial | ✅ Tested and completed |
| net | ✅ Tested and completed |
| openocd | 🔧 Pending testing |
| can | 🔧 Pending testing |

## License

MIT
