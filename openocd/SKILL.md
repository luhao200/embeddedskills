---
name: openocd
description: >-
  OpenOCD 下载与调试工具，用于探针探测、固件烧录、Flash 擦除、GDB Server 启动、目标复位控制、
  Telnet 在线调试（halt/resume/step/寄存器/内存/断点）、GDB 源码级调试，以及 Semihosting/ITM 输出捕获和底层查询。
  当用户提到 OpenOCD、ST-Link、CMSIS-DAP、DAPLink、FTDI、烧录固件、擦除 Flash、GDB Server、
  reset、interface/target/board 配置、openocd.cfg、在线调试、单步、断点、寄存器查看、
  内存读写、semihosting 时自动触发，也兼容 /openocd 显式调用。
  即使用户只是说"烧录一下"、"启动 GDB Server"、"擦除芯片"、"看看寄存器"、"单步调试"
  或"抓一下 semihosting"，只要上下文涉及 OpenOCD 支持的开源调试器就应触发此 skill。
argument-hint: "[probe|flash|erase|reset|reset-init|targets|flash-banks|adapter-info|raw|gdb-server|gdb|halt|resume|step|reg|read-mem|write-mem|bp|rbp|run-to|semihosting|itm] ..."
---

# OpenOCD 下载与调试

本 skill 提供 OpenOCD 的探针探测、固件烧录、Flash 擦除、GDB Server 启动、目标复位、
Telnet 在线调试、GDB 源码级调试和 Semihosting 输出捕获能力。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `exe` 路径正确：

```json
{
  "exe": "openocd",
  "scripts_dir": "",
  "default_board": "",
  "default_interface": "interface/stlink.cfg",
  "default_target": "target/stm32f4x.cfg",
  "default_file": "",
  "adapter_speed": "",
  "transport": "",
  "gdb_port": 3333,
  "telnet_port": 4444,
  "gdb_exe": "",
  "default_elf": "",
  "tpiu_name": "",
  "traceclk": "",
  "pin_freq": "",
  "operation_mode": 1
}
```

- `exe`：openocd.exe 路径或命令名（必填）
- `scripts_dir`：OpenOCD 配置脚本目录，为空时使用 OpenOCD 内置路径
- `default_board`：默认 board 配置（如 `board/stm32f4discovery.cfg`），优先级高于 interface+target
- `default_interface`：默认 interface 配置（如 `interface/stlink.cfg`）
- `default_target`：默认 target 配置（如 `target/stm32f4x.cfg`）
- `default_file` / `default_elf`：默认固件/ELF；为空时优先读取 `.embeddedskills/state.json`
- `adapter_speed` / `transport`：默认调试链路参数
- `gdb_port`：GDB Server 端口，默认 3333
- `telnet_port`：Telnet 端口，默认 4444
- `gdb_exe`：arm-none-eabi-gdb 路径，GDB 调试子命令（run/backtrace/locals）需要
- `tpiu_name` / `traceclk` / `pin_freq`：ITM/SWO 观测所需的 TPIU 参数
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

### 基础操作

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `probe` | 验证 board 或 interface+target 组合，探测目标连通性 | 低 |
| `flash` | 烧录固件（.elf / .hex / .bin） | 高 |
| `erase` | 擦除目标 Flash | 高 |
| `reset` / `reset-init` | 复位目标芯片 | 高 |
| `targets` / `flash-banks` / `adapter-info` | 查询底层 target / flash / adapter 信息 | 低 |
| `raw` | 执行受控 OpenOCD 原生命令 | 高 |

### GDB Server

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `gdb-server` | 启动 GDB Server，保持运行等待 GDB 连接 | 低 |
| `gdb backtrace/locals` | 快捷获取调用栈和局部变量 | 低 |
| `gdb break/continue/next/step/finish/until` | one-shot 执行流控制 | 低 |
| `gdb frame/print/watch/disassemble/threads/crash-report` | one-shot 源码级诊断 | 低 |

### Telnet 在线调试

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `halt` | 暂停 CPU，返回 PC/xPSR | 低 |
| `resume` | 恢复 CPU 运行 | 低 |
| `step` | 单步执行（支持 `--count N`） | 低 |
| `reg` | 查看所有 CPU 寄存器 | 低 |
| `read-mem` | 读内存（`--width 8/16/32`，`--length N`） | 低 |
| `write-mem` | 写内存（`--width 8/16/32`） | 高 |
| `bp` | 设置硬件断点 | 低 |
| `rbp` | 移除断点 | 低 |
| `run-to` | 运行到指定地址（设置断点 + resume + 等待命中） | 低 |

### Semihosting

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `semihosting` | 启用 ARM Semihosting 并捕获目标 printf 输出 | 低 |
| `itm` | 基于 TPIU/ITM 读取 SWO/ITM 观测数据 | 低 |

## 执行流程

1. 读取 `config.json`，确认 `exe` 路径有效
2. 读取默认 `board / interface / target` 配置
3. 已知 `board` 时优先使用 `-f board/*.cfg`，否则组合 `-f interface/*.cfg -f target/*.cfg`
4. `board`、`interface`、`target` 同时缺失时，不自动拼接组合，直接要求用户补充
5. 按 `operation_mode` 决定是否需要确认后执行
6. 根据子命令调用对应脚本
7. 统一解析输出中的 `Info`、`Error` 和就绪日志，返回结构化结果

## 脚本调用

skill 目录下有五个 Python 脚本，使用标准库实现，无额外依赖。

### openocd_run.py — 探测 / 烧录 / 擦除 / 复位

```bash
# 探测连通性（使用 board）
python <skill-dir>/scripts/openocd_run.py probe --board board/stm32f4discovery.cfg --json

# 探测连通性（使用 interface + target）
python <skill-dir>/scripts/openocd_run.py probe --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 烧录 ELF 固件
python <skill-dir>/scripts/openocd_run.py flash --file build/app.elf --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 烧录 BIN 固件（必须提供地址）
python <skill-dir>/scripts/openocd_run.py flash --file build/app.bin --address 0x08000000 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 擦除 Flash（自动选择 mass/sector）
python <skill-dir>/scripts/openocd_run.py erase --mode auto --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 复位目标
python <skill-dir>/scripts/openocd_run.py reset --mode halt --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 查询 target 列表
python <skill-dir>/scripts/openocd_run.py targets --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 查询 flash bank
python <skill-dir>/scripts/openocd_run.py flash-banks --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 执行受控 raw 命令
python <skill-dir>/scripts/openocd_run.py raw --interface interface/stlink.cfg --target target/stm32f4x.cfg --command "init" "targets" --json
```

通用可选参数：`--board <cfg>`、`--search <目录>`、`--adapter-speed <kHz>`、`--transport <swd|jtag>`、`--exe <openocd路径>`

### openocd_gdb.py — GDB Server 启动与调试

```bash
# 启动 GDB Server（保持运行）
python <skill-dir>/scripts/openocd_gdb.py server --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 无子命令时默认 server（向后兼容）
python <skill-dir>/scripts/openocd_gdb.py --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 执行自定义 GDB 命令序列
python <skill-dir>/scripts/openocd_gdb.py run --gdb-exe arm-none-eabi-gdb --elf build/app.elf --interface interface/stlink.cfg --target target/stm32f4x.cfg --commands "break main" "continue" "backtrace" "info locals" --json

# 快捷获取调用栈
python <skill-dir>/scripts/openocd_gdb.py backtrace --gdb-exe arm-none-eabi-gdb --elf build/app.elf --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 快捷查看局部变量
python <skill-dir>/scripts/openocd_gdb.py locals --gdb-exe arm-none-eabi-gdb --elf build/app.elf --interface interface/stlink.cfg --target target/stm32f4x.cfg --json
```

可选参数：`--gdb-port`、`--telnet-port`、`--search`、`--adapter-speed`、`--transport`、`--board`

### openocd_telnet.py — Telnet 在线调试

```bash
# 暂停 CPU
python <skill-dir>/scripts/openocd_telnet.py halt --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 恢复运行
python <skill-dir>/scripts/openocd_telnet.py resume --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 单步 5 次
python <skill-dir>/scripts/openocd_telnet.py step --count 5 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 查看寄存器
python <skill-dir>/scripts/openocd_telnet.py reg --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 读内存（32bit x 16 个字）
python <skill-dir>/scripts/openocd_telnet.py read-mem --address 0x20000000 --length 16 --width 32 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 写内存
python <skill-dir>/scripts/openocd_telnet.py write-mem --address 0x20000000 --value 0xDEADBEEF --width 32 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 设置硬件断点
python <skill-dir>/scripts/openocd_telnet.py bp --address 0x08001234 --bp-length 2 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 移除断点
python <skill-dir>/scripts/openocd_telnet.py rbp --address 0x08001234 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 运行到指定地址（设置断点 + resume + 等待）
python <skill-dir>/scripts/openocd_telnet.py run-to --address 0x08001234 --timeout-ms 3000 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json
```

通用可选参数：`--board`、`--search`、`--adapter-speed`、`--transport`、`--gdb-port`、`--telnet-port`

### openocd_semihosting.py — Semihosting 输出捕获

```bash
# 捕获 semihosting 输出（持续到 Ctrl+C）
python <skill-dir>/scripts/openocd_semihosting.py --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 捕获 30 秒
python <skill-dir>/scripts/openocd_semihosting.py --timeout 30 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json
```

### openocd_itm.py — ITM/SWO 观测

```bash
# 使用配置中的 TPIU 参数持续读取 ITM
python <skill-dir>/scripts/openocd_itm.py --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 指定 TPIU 名称和端口
python <skill-dir>/scripts/openocd_itm.py \
  --interface interface/stlink.cfg --target target/stm32f4x.cfg \
  --tpiu-name stm32f4x.tpiu --traceclk 168000000 --pin-freq 2000000 \
  --itm-port 0 --itm-port 1 --json
```

`openocd_itm.py` 会启动独立 OpenOCD Server，打开 TPIU/ITM 配置后，把 trace 输出统一包装成 JSON Lines。

## 调试典型工作流

### 快速检查（Telnet）

```
halt -> reg -> read-mem 0x20000000 -> step -> resume
```

适合快速查看当前 CPU 状态和内存内容。

### 断点调试（Telnet）

```
run-to 0x08001234 -> reg -> read-mem -> resume
```

运行到指定地址后暂停，检查寄存器和内存。

### 源码级调试（GDB）

```bash
gdb run --elf app.elf --commands "break main" "continue" "backtrace" "info locals"
```

使用 ELF 文件提供符号信息，进行函数级断点和变量查看。

### Semihosting 输出

```bash
semihosting
```

捕获目标通过 `printf`（SVC 指令）输出的调试信息，类似 J-Link RTT。

## 输出格式

所有脚本以 JSON 格式返回，基础字段为 `status`（ok/error）、`action`、`summary`、`details`，并可能附带 `context`、`artifacts`、`metrics`、`state`、`next_actions`、`timing`。流式观测命令使用 JSON Lines，并统一输出 `source`、`channel_type`、`stream_type`。

成功示例：
```json
{
  "status": "ok",
  "action": "halt",
  "summary": "已暂停，PC=0x08000298",
  "details": {
    "pc": "0x08000298",
    "xpsr": "0x01000000",
    "msp": "0x20020000",
    "halted": true
  }
}
```

GDB 调用栈示例：
```json
{
  "status": "ok",
  "action": "backtrace",
  "summary": "GDB backtrace 执行成功",
  "details": {
    "gdb_port": 3333,
    "frames": [
      {"frame": 0, "function": "main", "location": "src/main.c:42"}
    ]
  }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "halt",
  "error": {
    "code": "server_failed",
    "message": "OpenOCD 启动失败或超时"
  }
}
```

## 核心规则

- 不自动猜测 `board`、`interface`、`target` 的组合，缺失时必须询问用户
- 已知 `board` 时优先使用 board 配置，不再需要 interface+target
- 参数解析优先级为：CLI 显式参数 > `config.json` > `.embeddedskills/state.json` > 报错
- `.bin` 文件必须显式提供烧录地址，缺失时报错
- STM32F4 等已映射 target 在 `erase --mode auto` 下会先 `reset halt`，再优先使用 mass erase
- `erase --mode mass` 在未命中映射时直接返回 `mass_erase_unsupported`，避免误以为已做整片擦除
- 检测到 Flash 锁保护时只提示，不自动解锁
- 连接失败时给出排查建议（检查连线、供电、驱动、cfg 路径），不自动尝试更激进参数
- 烧录、擦除、复位在参数完整且用户意图明确时直接执行
- `gdb-server` 启动后返回端口和连接方式，进程保持运行
- GDB 调试（run/backtrace/locals）需要配置 `gdb_exe`（arm-none-eabi-gdb 路径）
- Telnet 调试命令每次启动独立的 OpenOCD Server，执行完自动关闭
- Semihosting 通过 Telnet 启用后持续读取 OpenOCD stderr 输出
- 结果回显中始终包含 cfg 组合、端口和执行动作

## 参考

遇到 board/interface/target 配置问题时可查阅 `references/common_targets.md`。
