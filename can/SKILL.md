---
name: can
description: >-
  嵌入式 CAN / CAN-FD 调试工具，用于扫描接口、监控报文、发送测试帧、记录日志、数据库文件解码和总线统计。
  当用户提到 CAN、CAN-FD、DBC 解码、总线抓包、USB-CAN 联调、报文发送、总线统计、
  PCAN、Vector、slcan、CAN 接口扫描、CAN ID 过滤、ASC 日志、BLF 文件时自动触发，
  也兼容 /can 显式调用。即使用户只是说"看看 CAN 报文"、"发一帧试试"或"解码一下 DBC"，
  只要上下文涉及 CAN 总线通信就应触发此 skill。
argument-hint: "[scan|monitor|send|log|decode|stats] ..."
---

# CAN — 嵌入式 CAN / CAN-FD 调试工具

统一封装接口发现、实时监控、报文发送、日志记录、数据库文件解码和统计分析能力。

## 配置

Skill 目录下的 `config.json` 存放默认连接参数，所有脚本从此处读取 CAN 配置：

```json
{
  "default_interface": "",
  "default_channel": "",
  "default_bitrate": 0,
  "default_data_bitrate": 0,
  "default_log_dir": ".logs",
  "slcan_serial_port": "",
  "slcan_serial_baudrate": 115200
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `default_interface` | CAN 后端，如 `pcan` / `vector` / `slcan` | `""` |
| `default_channel` | 通道名，如 `PCAN_USBBUS1` | `""` |
| `default_bitrate` | 仲裁域比特率，`0` 表示未设置 | `0` |
| `default_data_bitrate` | CAN-FD 数据域比特率，`0` 表示未设置 | `0` |
| `default_log_dir` | 日志输出目录 | `.logs` |
| `slcan_serial_port` | slcan 场景的串口 | `""` |
| `slcan_serial_baudrate` | slcan 场景的串口速率 | `115200` |

连接参数（interface、channel、bitrate、data_bitrate）只从 `config.json` 读取，脚本不通过命令行接收这些参数。`decode` 使用位置参数显式传入数据库文件。若配置缺失或连接失败，询问用户并引导其修改 `config.json`。

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 扫描可用 CAN 接口与 USB-CAN 设备 | 低 |
| `monitor` | 实时监控总线报文 | 低 |
| `send` | 发送标准帧 / 扩展帧 / 远程帧 / CAN-FD 帧 | 高 |
| `log` | 记录总线报文到 ASC / BLF / CSV 文件 | 低 |
| `decode` | 用 DBC 等数据库文件解码报文或日志 | 低 |
| `stats` | 统计总线负载、ID 分布和帧率 | 低 |

## 执行流程

1. 检查 `python-can` 是否可用，未安装时提示 `pip install python-can`
2. 读取 `config.json`，合并默认连接参数
3. 无子命令时默认执行 `scan`
4. `monitor / send / log / stats` 使用 config.json 中的连接参数
5. `decode` 先确认数据库文件和输入源存在
6. 若配置缺少必要项或连接失败，询问用户
7. `send` 只要配置可连接就直接执行，不二次确认
8. 运行对应脚本并输出结构化结果
9. 失败时优先反馈接口、驱动、比特率和过滤条件问题

## 脚本调用

所有脚本位于 skill 目录的 `scripts/` 下，通过 `python` 直接调用。
脚本会自动读取同级目录的 `config.json`。

```bash
# 扫描接口
python scripts/can_scan.py [--json]

# 实时监控
python scripts/can_monitor.py [--fd] [--filter-id <ID列表>] [--exclude-id <ID列表>] [--dbc <DBC文件>] [--timeout <秒>] [--json]

# 发送报文
python scripts/can_send.py <id> <data> [--extended] [--remote] [--fd] [--repeat <次>] [--interval <秒>] [--periodic <毫秒>] [--listen] [--json]

# 日志记录
python scripts/can_log.py [--output <文件>] [--duration <秒>] [--max-count <数量>] [--filter-id <ID列表>] [--console] [--json]

# 数据库解码
python scripts/can_decode.py <db_file> [--db-format <auto|dbc|arxml|kcd|sym|cdd>] [--id <CAN_ID>] [--data <HEX数据>] [--log <日志文件>] [--signal <信号名>] [--list] [--json]

# 总线统计
python scripts/can_stats.py [--duration <秒>] [--top <数量>] [--watch <ID列表>] [--json]
```

## 输出格式

单次命令返回标准 JSON：
```json
{
  "status": "ok",
  "action": "scan",
  "summary": "发现 2 个 CAN 接口",
  "details": { ... }
}
```

持续命令（monitor --json、send --listen --json）输出 JSON Lines，结束摘要写入 stderr。

错误输出：
```json
{
  "status": "error",
  "action": "send",
  "error": { "code": "interface_open_failed", "message": "无法打开指定 CAN 接口" }
}
```

## 核心规则

- 不自动猜测 interface、channel、bitrate，多接口时不自动选择
- 连接参数仅来自 config.json，不通过命令行传递
- 未明确说明用途时不主动发送任何报文
- `--json` 输出的持续流使用 JSON Lines，摘要写 stderr 不污染数据流
- DBC 解码失败不应导致监控中断
- 找不到帧定义时返回明确错误，不静默吞掉

## 参考

- `references/common_interfaces.json`：常见 USB-CAN 设备信息
