---
name: serial
description: >-
  嵌入式串口调试工具，用于扫描串口、实时监控、发送数据、记录日志和 Hex 查看。
  当用户提到串口、COM 口、UART、AT 命令调试、波特率、Hex 串流、串口抓日志、
  串口监控、查看 MCU 输出、二进制协议联调时自动触发，也兼容 /serial 显式调用。
  即使用户只是说"看看串口输出"、"发个 AT 命令"或"抓一下日志"，只要上下文涉及
  串口通信就应触发此 skill。
argument-hint: "[scan|monitor|send|hex|log] ..."
---

# Serial — 嵌入式串口调试工具

统一封装端口发现、实时监控、数据发送、日志记录和 Hex 查看能力。

## 配置

### 环境级配置 (`skill/config.json`)

serial skill 的环境级配置目前为空对象 `{}`，因为串口参数属于工程级配置，统一在工作区的 `.embeddedskills/config.json` 中管理。

### 工程级配置 (`.embeddedskills/config.json`)

工作区下的 `.embeddedskills/config.json` 存放工程级串口配置：

```json
{
  "serial": {
    "port": "",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "none",
    "stopbits": 1,
    "encoding": "utf-8",
    "timeout_sec": 1.0,
    "log_dir": ".embeddedskills/logs/serial"
  }
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `port` | 串口号，如 `COM3` | `""` |
| `baudrate` | 波特率 | `115200` |
| `bytesize` | 数据位 | `8` |
| `parity` | 校验位：none/even/odd/mark/space | `none` |
| `stopbits` | 停止位：1/1.5/2 | `1` |
| `encoding` | 文本编码 | `utf-8` |
| `timeout_sec` | 读写超时（秒） | `1.0` |
| `log_dir` | 日志输出目录 | `.embeddedskills/logs/serial` |

### 参数解析优先级

1. **CLI 参数** (`--port`, `--baudrate` 等) - 最高优先级
2. **工程级配置** (`.embeddedskills/config.json` 中的 `serial` 部分)
3. **状态文件** (`.embeddedskills/state.json` 中的历史记录)
4. **默认值** - 最低优先级

### 自动扫描行为

当未指定 `port` 时，脚本会自动扫描系统串口：
- 若只找到一个串口，自动使用该端口并写入工程配置
- 若找到多个串口，返回候选列表让用户选择（通过 `--port` 指定）
- 若未找到串口，提示错误

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 扫描可用串口 | 低 |
| `monitor` | 实时查看文本输出 | 低 |
| `send` | 发送文本或 Hex 数据 | 中 |
| `hex` | 实时查看二进制流 | 低 |
| `log` | 保存串口日志到文件 | 低 |

## 执行流程

1. 检查 `pyserial` 是否可用，未安装时提示 `pip install pyserial`
2. 按优先级解析参数：CLI > 工程级配置 > 状态文件 > 默认值
3. 无子命令时默认执行 `scan`
4. `monitor / send / hex / log` 使用解析后的连接参数
5. 若未指定 `port`，自动扫描系统串口：
   - 唯一候选：自动使用并写入工程配置
   - 多候选：返回列表让用户选择
6. 成功执行后，将确认的参数写回工程配置
7. 运行对应脚本并输出结构化结果
8. 失败时优先反馈端口占用、驱动、波特率和编码问题

## 脚本调用

所有脚本位于 skill 目录的 `scripts/` 下，通过 `python` 直接调用。
脚本会按优先级从 CLI 参数、工程级配置、状态文件中读取参数。

```bash
# 扫描串口
python scripts/serial_scan.py [--filter <关键词>] [--json]

# 实时监控
python scripts/serial_monitor.py [--port <串口>] [--baudrate <波特率>] [--timestamp] [--filter <regex>] [--timeout <秒>] [--json]

# 发送数据
python scripts/serial_send.py [--port <串口>] [--baudrate <波特率>] <data> [--hex] [--crlf] [--repeat <次>] [--wait-response] [--json]

# Hex 查看
python scripts/serial_hex.py [--port <串口>] [--baudrate <波特率>] [--width <列>] [--timeout <秒>] [--json]

# 日志记录
python scripts/serial_log.py [--port <串口>] [--baudrate <波特率>] [--output <文件>] [--duration <秒>] [--format text|csv|json] [--json]
```

## 输出格式

单次命令返回标准 JSON：
```json
{
  "status": "ok",
  "action": "scan",
  "summary": "发现 2 个串口",
  "details": { ... }
}
```

持续命令（monitor --json、hex --json）输出 JSON Lines，结束摘要写入 stderr。

错误输出：
```json
{
  "status": "error",
  "action": "monitor",
  "error": { "code": "port_busy", "message": "串口被其他程序占用" }
}
```

## 串口多路复用 (Mux)

当需要同时使用 minicom（或其他串口工具）和 skill 脚本访问同一个串口设备时，可以通过 mux 后台服务实现多路复用。

### 依赖

- **socat** — `apt install socat` / `pacman -S socat`

### 架构

```
                   ┌──────────────────┐
                   │   Real Hardware   │
                   │   /dev/ttyUSB0    │
                   └────────┬─────────┘
                            │
                   ┌────────▼─────────┐
                   │ Python mux server │
                   │ TCP-LISTEN:20001  │  单串口读者 + 广播
                   └────────┬─────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
   ┌────────▼──────┐ ┌─────▼──────┐ ┌──────▼────────┐
   │  socat PTY    │ │ skill      │ │ skill         │
   │ /tmp/serial_  │ │ monitor    │ │ send/log/hex  │
   │ mux_vserial   │ │ socket://  │ │ socket://     │
   └───────┬───────┘ └────────────┘ └───────────────┘
           │
   ┌───────▼───────┐
   │   minicom     │
   │  (用户侧)      │
   └───────────────┘
```

- **Layer 1**: Python mux 进程独占打开真实串口，暴露 TCP server，并把串口 RX 广播给所有客户端
- **Layer 2**: socat 作为 TCP 客户端创建虚拟 PTY `/tmp/serial_mux_vserial`，供 minicom 使用
- **Skill 脚本**: 自动检测 mux 状态，仅当本次串口配置与 mux 匹配时通过 `socket://` 连接 TCP 端口
- **数据流**: 串口 RX → 广播到所有 TCP 客户端；任一客户端 TX → 转发到真实串口

### Mux 管理命令

```bash
# 启动多路复用
python scripts/serial_mux.py start --port /dev/ttyUSB0 [--baudrate 115200]

# 查询状态
python scripts/serial_mux.py status

# 停止多路复用
python scripts/serial_mux.py stop
```

启动后，skill 脚本（monitor/hex/log/send）自动通过多路复用连接，无需额外参数。若命令显式指定了不同串口或串口参数，则不会复用当前 mux。

### 使用流程

1. `python scripts/serial_mux.py start --port /dev/ttyUSB0`
2. `minicom -D /tmp/serial_mux_vserial`（用户侧交互）
3. `python scripts/serial_monitor.py`（模型侧监控，自动走 mux）
4. 两个终端同时看到串口数据
5. `python scripts/serial_mux.py stop` 停止复用（终止 socat 进程并清理 `/tmp/serial_mux_vserial` 符号链接）

### 写入冲突警告

**多客户端同时写入会导致串口数据错乱。** 监控/hex/log 脚本在通过 mux 连接时输出警告到 stderr。send 脚本输出更强的冲突警告。如需直连真实串口（跳过 mux），使用 `--direct` 参数。

### Mux 状态持久化

mux 进程 PID 保存到 `.embeddedskills/state.json` 的 `serial_mux` 段。脚本退出后下次调用 `status` 会检测进程是否仍存活，自动清理僵尸 PID。`start` 成功后会把已确认的串口配置写回 `.embeddedskills/config.json`，方便后续无参命令复用。`stop` 命令会终止 mux 与 socat PTY 进程，并删除残留的 `/tmp/serial_mux_vserial` 符号链接。

## 核心规则

- 不自动猜测端口和波特率，发现多个候选串口时不自动选择
- 参数解析优先级：CLI > 工程级配置 > 状态文件 > 默认值
- 未指定 `port` 时自动扫描，唯一候选自动写入配置，多候选需用户选择
- 成功执行后，确认的参数自动写回 `.embeddedskills/config.json`
- 未明确说明用途时不主动发送任何串口数据
- `--json` 输出的持续流使用 JSON Lines，摘要写 stderr 不污染数据流
- 正则过滤失败不应导致监控退出
- Mux 运行中发送数据前提示用户避免同时写入

## 参考

- `references/common_devices.json`：常见 USB 转串口芯片 VID/PID 映射
