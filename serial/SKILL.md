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

Skill 目录下的 `config.json` 存放默认连接参数，所有脚本从此处读取串口配置：

```json
{
  "default_port": "",
  "default_baudrate": 115200,
  "default_bytesize": 8,
  "default_parity": "none",
  "default_stopbits": 1,
  "default_encoding": "utf-8",
  "default_timeout_sec": 1.0,
  "default_log_dir": ".logs"
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `default_port` | 串口号，如 `COM3` | `""` |
| `default_baudrate` | 波特率 | `115200` |
| `default_bytesize` | 数据位 | `8` |
| `default_parity` | 校验位：none/even/odd/mark/space | `none` |
| `default_stopbits` | 停止位：1/1.5/2 | `1` |
| `default_encoding` | 文本编码 | `utf-8` |
| `default_timeout_sec` | 读写超时（秒） | `1.0` |
| `default_log_dir` | 日志输出目录 | `.logs` |

连接参数（port、baudrate、bytesize、parity、stopbits、encoding）只从 `config.json` 读取，脚本不通过命令行接收这些参数。若配置缺失或连接失败，询问用户并引导其修改 `config.json`。

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
2. 读取 `config.json`，合并默认连接参数
3. 无子命令时默认执行 `scan`
4. `monitor / send / hex / log` 使用 config.json 中的连接参数
5. 若配置缺少必要项或连接失败，询问用户
6. `send` 只要配置可连接就直接执行，不二次确认
7. 运行对应脚本并输出结构化结果
8. 失败时优先反馈端口占用、驱动、波特率和编码问题

## 脚本调用

所有脚本位于 skill 目录的 `scripts/` 下，通过 `python` 直接调用。
脚本会自动读取同级目录的 `config.json`。

```bash
# 扫描串口
python scripts/serial_scan.py [--filter <关键词>] [--json]

# 实时监控
python scripts/serial_monitor.py [--timestamp] [--filter <regex>] [--timeout <秒>] [--json]

# 发送数据
python scripts/serial_send.py <data> [--hex] [--crlf] [--repeat <次>] [--wait-response] [--json]

# Hex 查看
python scripts/serial_hex.py [--width <列>] [--timeout <秒>] [--json]

# 日志记录
python scripts/serial_log.py [--output <文件>] [--duration <秒>] [--format text|csv|json] [--json]
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

## 核心规则

- 不自动猜测端口和波特率，发现多个候选串口时不自动选择
- 连接参数仅来自 config.json，不通过命令行传递
- 未明确说明用途时不主动发送任何串口数据
- `--json` 输出的持续流使用 JSON Lines，摘要写 stderr 不污染数据流
- 正则过滤失败不应导致监控退出

## 参考

- `references/common_devices.json`：常见 USB 转串口芯片 VID/PID 映射
