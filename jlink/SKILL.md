---
name: jlink
description: >-
  J-Link 下载与在线调试工具，用于探测设备、烧录固件、读写内存、查看寄存器、复位目标、读取 RTT/SWO 日志，
  以及在线调试（暂停/恢复/单步/断点运行/调用栈/变量查看）。
  当用户提到 J-Link、JLink、RTT、烧录固件、写内存、读内存、寄存器查看、目标复位、探针连通性检查、
  在线调试、单步、断点、调用栈时自动触发，也兼容 /jlink 显式调用。
  即使用户只是说"烧录一下"、"看看 RTT 输出"或"调试一下"，只要上下文涉及 J-Link 探针就应触发此 skill。
argument-hint: "[info|flash|read-mem|write-mem|regs|reset|halt|go|step|run-to|rtt|swo|gdb] ..."
---

# J-Link 下载与在线调试

本 skill 提供 J-Link 探针的设备探测、固件烧录、内存读写、寄存器查看、目标复位、RTT 日志读取，以及轻量在线调试和 GDB 源码级调试能力。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `exe` 路径正确：

```json
{
  "exe": "C:\\Program Files\\SEGGER\\JLink\\JLink.exe",
  "gdbserver_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkGDBServerCL.exe",
  "rtt_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkRTTClient.exe",
  "gdb_exe": "C:\\Program Files\\Arm\\GNU Toolchain mingw-w64-x86_64-arm-none-eabi\\bin\\arm-none-eabi-gdb.exe",
  "default_elf": "",
  "default_device": "",
  "default_interface": "SWD",
  "default_speed": "4000",
  "serial_no": "",
  "rtt_telnet_port": 0,
  "swo_command": [],
  "operation_mode": 1
}
```

- `exe`：JLink.exe 完整路径（必填）
- `gdbserver_exe`：JLinkGDBServerCL.exe 路径，RTT 和 GDB 调试需要
- `rtt_exe`：JLinkRTTClient.exe 路径
- `gdb_exe`：arm-none-eabi-gdb 路径，GDB 源码级调试需要
- `default_elf`：默认 ELF 路径；为空时优先读取 `.embeddedskills/state.json`
- `default_device`：默认芯片型号（如 GD32F470ZG），为空时需用户指定
- `default_interface`：调试接口，SWD 或 JTAG，默认 SWD
- `default_speed`：调试速率 kHz，默认 4000
- `serial_no`：默认探针序列号，多探针场景下使用
- `rtt_telnet_port`：RTT 端口，0 表示使用工具默认值
- `swo_command`：可选，完整 SWO viewer 命令数组，供 `jlink_swo.py` 包装
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

### 基础操作

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `info` | 探测探针与目标连通性 | 低 |
| `flash` | 烧录固件（.hex / .bin / .elf） | 高 |
| `read-mem` | 读取内存区域 | 低 |
| `write-mem` | 写入内存 | 高 |
| `regs` | 查看 CPU 寄存器 | 低 |
| `reset` | 复位目标芯片 | 高 |
| `rtt` | 读取 RTT 日志输出 | 低 |
| `swo` | 包装外部 SWO viewer 为统一事件流 | 低 |

### 在线调试（JLink Commander）

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `halt` | 暂停 CPU，返回寄存器状态 | 低 |
| `go` | 恢复 CPU 运行 | 低 |
| `step` | 单步执行（支持指定步数），返回执行的指令和寄存器 | 低 |
| `run-to` | 设置断点并运行，等待命中后返回状态 | 低 |

### GDB 源码级调试

| 子命令 | 用途 | 依赖 |
|--------|------|------|
| `gdb backtrace/locals` | 查看调用栈和局部变量 | arm-none-eabi-gdb |
| `gdb break/continue/next/step/finish/until` | one-shot 控制执行流 | arm-none-eabi-gdb |
| `gdb frame/print/watch/disassemble/threads/crash-report` | one-shot 源码级诊断 | arm-none-eabi-gdb |

## 执行流程

1. 读取 `config.json`，确认 `exe` 路径有效
2. 读取默认 `device / interface / speed / serial_no`
3. 若当前动作需要 `device` 且仍为空，直接要求用户补充，绝不猜测
4. 多探针场景未指定 `serial_no` 时，列出探针让用户选择，不自动选择
5. 按 `operation_mode` 决定是否需要确认后执行
6. 使用模板生成临时 `.jlink` 命令文件，调用 JLink.exe 时带 `-NoGui 1 -ExitOnError 1 -AutoConnect 1`
7. 解析输出和返回码，返回结构化结果

## 脚本调用

skill 目录下有四个 Python 脚本，使用标准库实现，无额外依赖。

### jlink_exec.py — 基础操作 + 轻量调试

```bash
# 探测连通性
python <skill-dir>/scripts/jlink_exec.py info --device GD32F470ZG --json

# 烧录固件
python <skill-dir>/scripts/jlink_exec.py flash --file build/app.hex --device GD32F470ZG --json

# 烧录 .bin（必须提供地址）
python <skill-dir>/scripts/jlink_exec.py flash --file build/app.bin --device GD32F470ZG --address 0x08000000 --json

# 读取内存
python <skill-dir>/scripts/jlink_exec.py read-mem --address 0x08000000 --length 256 --device GD32F470ZG --json

# 写入内存
python <skill-dir>/scripts/jlink_exec.py write-mem --address 0x20000000 --value 0x12345678 --device GD32F470ZG --json

# 查看寄存器
python <skill-dir>/scripts/jlink_exec.py regs --device GD32F470ZG --json

# 复位目标
python <skill-dir>/scripts/jlink_exec.py reset --device GD32F470ZG --json

# 暂停 CPU
python <skill-dir>/scripts/jlink_exec.py halt --device GD32F470ZG --json

# 恢复运行
python <skill-dir>/scripts/jlink_exec.py go --device GD32F470ZG --json

# 单步执行（3 步）
python <skill-dir>/scripts/jlink_exec.py step --device GD32F470ZG --count 3 --json

# 运行到断点地址
python <skill-dir>/scripts/jlink_exec.py run-to --device GD32F470ZG --address 0x08001234 --timeout-ms 3000 --json
```

通用可选参数：`--interface SWD|JTAG`、`--speed 4000`、`--serial-no <序列号>`、`--exe <JLink.exe路径>`

### jlink_rtt.py — RTT 日志读取

```bash
python <skill-dir>/scripts/jlink_rtt.py --device GD32F470ZG --json
```

可选参数：`--serial-no`、`--channel`、`--encoding`、`--rtt-port`、`--gdbserver-exe <路径>`、`--rtt-exe <路径>`

RTT 工作原理：脚本先通过 JLinkGDBServerCL.exe 建立调试连接，再启动 JLinkRTTClient.exe 读取 RTT 数据。`--json` 模式输出 JSON Lines。

### jlink_swo.py — SWO 事件流包装

```bash
# 使用 config.json 里的 swo_command
python <skill-dir>/scripts/jlink_swo.py --json

# 或显式传入 viewer 命令
python <skill-dir>/scripts/jlink_swo.py \
  --viewer-cmd JLinkSWOViewerCL.exe -device GD32F470ZG -itf SWD -speed 4000 \
  --json
```

`jlink_swo.py` 不直接实现 SWO 协议，而是把外部 viewer 的 stdout/stderr 统一包装成 JSON Lines，便于上层 workflow 或 AI 继续消费。

### jlink_gdb.py — GDB 源码级调试（需要 arm-none-eabi-gdb）

```bash
# 执行自定义 GDB 命令序列
python <skill-dir>/scripts/jlink_gdb.py run \
  --gdbserver-exe <路径> --gdb-exe <arm-none-eabi-gdb路径> \
  --device GD32F470ZG --elf build/app.elf \
  --commands "break main" "continue" "backtrace" "info locals" --json

# 快捷：获取调用栈
python <skill-dir>/scripts/jlink_gdb.py backtrace \
  --gdbserver-exe <路径> --gdb-exe <路径> \
  --device GD32F470ZG --elf build/app.elf --json

# 快捷：查看局部变量
python <skill-dir>/scripts/jlink_gdb.py locals \
  --gdbserver-exe <路径> --gdb-exe <路径> \
  --device GD32F470ZG --elf build/app.elf --json
```

GDB 调试需要 ELF 文件才能进行源码级调试（断点到函数名、查看变量）。没有 ELF 时仍可使用地址级调试。

## 输出格式

所有脚本以 JSON 格式返回，基础字段为 `status`（ok/error）、`action`、`summary`、`details`，并可能附带 `context`、`artifacts`、`metrics`、`state`、`next_actions`、`timing`。流式观测命令使用 JSON Lines，并统一输出 `source`、`channel_type`、`stream_type`。

成功示例：
```json
{
  "status": "ok",
  "action": "halt",
  "summary": "已暂停，PC=0x08049ABC",
  "details": {
    "device": "GD32F470ZG",
    "registers": { "PC": "0x08049ABC", "R0": "0x00000004", "..." : "..." }
  }
}
```

step 示例（包含执行的指令）：
```json
{
  "status": "ok",
  "action": "step",
  "summary": "单步3次，PC=0x08049AB4",
  "details": {
    "steps": [
      { "address": "0x08049AB8", "opcode": "80 1B", "instruction": "SUBS R0, R0, R6" },
      { "address": "0x08049ABA", "opcode": "A8 42", "instruction": "CMP R0, R5" },
      { "address": "0x08049ABC", "opcode": "FA D3", "instruction": "BCC #-0x0C" }
    ],
    "registers": { "PC": "0x08049AB4", "..." : "..." }
  }
}
```

run-to 示例（断点命中）：
```json
{
  "status": "ok",
  "action": "run-to",
  "summary": "断点命中 @ 0x08049AB4，PC=0x08049AB4",
  "details": {
    "bp_address": "0x08049AB4",
    "bp_hit": true,
    "registers": { "PC": "0x08049AB4", "..." : "..." }
  }
}
```

## 核心规则

- 不自动猜测 `device` 芯片型号，缺失时必须询问用户
- 多探针场景不自动选择探针，必须让用户指定序列号
- 参数解析优先级为：CLI 显式参数 > `config.json` > `.embeddedskills/state.json` > 报错
- `.bin` 文件必须显式提供烧录地址，缺失时报错
- 连接失败时给出排查建议（检查连线、供电、接口类型、速度），不自动尝试更激进参数
- 烧录、写内存、复位在参数完整且用户意图明确时直接执行
- `run-to` 的断点在单次 JLink 会话内完成设置和清除，不存在跨会话 handle 问题
- 结果回显中始终包含目标芯片、接口类型和执行动作

## 调试典型工作流

### 快速排查（JLink Commander）

```
halt → regs → read-mem → step → go
```
适合查看当前执行位置、寄存器状态、内存值，无需 ELF 和 GDB。

### 断点调试（JLink Commander）

```
run-to(address) → regs → read-mem → go
```
在指定地址设置断点并等待命中，查看此时的状态。

### 源码级调试（GDB）

```
gdb run --elf app.elf --commands "break main" "continue" "backtrace" "info locals"
```
需要 ELF 文件和 arm-none-eabi-gdb，支持函数名断点和变量查看。

## 参考

遇到芯片型号问题时可查阅 `references/common_devices.md`。
