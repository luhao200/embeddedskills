# can

Claude Code skill，用于嵌入式 CAN / CAN-FD 总线调试：接口扫描、实时监控、报文发送、日志记录、DBC 解码和总线统计。

## 功能

- 扫描系统可用 CAN 接口与 USB-CAN 设备
- 实时监控总线报文（支持 ID 过滤、DBC 解码、CAN-FD）
- 发送标准帧 / 扩展帧 / 远程帧 / CAN-FD 帧（支持周期发送和回听）
- 记录总线报文到 ASC / BLF / CSV 文件
- 用 DBC / ARXML / KCD 等数据库文件解码报文或日志
- 统计总线负载、ID 分布和帧率

## 环境要求

- Python 3.x
- [python-can](https://python-can.readthedocs.io/) — `pip install python-can`
- [cantools](https://cantools.readthedocs.io/) — `pip install cantools`
- [pyserial](https://pypi.org/project/pyserial/) — `pip install pyserial`（仅 slcan 场景需要）
- USB-CAN 设备驱动（PEAK、Vector、Kvaser 等，按硬件安装对应驱动）

## 配置

复制 `config.example.json` 为 `config.json`，根据实际环境修改：

```json
{
  "default_interface": "",
  "default_channel": "",
  "default_bitrate": 0,
  "default_data_bitrate": 0,
  "default_db_file": "",
  "default_log_dir": ".logs",
  "slcan_serial_port": "",
  "slcan_serial_baudrate": 115200
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `default_interface` | 否 | CAN 后端，如 `pcan` / `vector` / `slcan` |
| `default_channel` | 否 | 通道名，如 `PCAN_USBBUS1` |
| `default_bitrate` | 否 | 仲裁域比特率，`0` 表示未设置 |
| `default_data_bitrate` | 否 | CAN-FD 数据域比特率，`0` 表示未设置 |
| `default_db_file` | 否 | 默认数据库文件路径 |
| `default_log_dir` | 否 | 日志输出目录，默认 `.logs` |
| `slcan_serial_port` | 否 | slcan 场景的串口号 |
| `slcan_serial_baudrate` | 否 | slcan 场景的串口速率，默认 115200 |

> 注意：所有连接参数仅从 `config.json` 读取，不通过命令行传递。

## 子命令

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `scan` | 扫描可用 CAN 接口（默认子命令） | `/can scan` |
| `monitor` | 实时监控总线报文 | `/can monitor --timeout 10` |
| `send` | 发送测试帧 | `/can send 0x123 "DE AD BE EF"` |
| `log` | 记录总线日志 | `/can log --output trace.asc` |
| `decode` | 用数据库文件解码报文或日志 | `/can decode vehicle.dbc --log trace.asc` |
| `stats` | 统计总线负载与 ID 分布 | `/can stats --duration 10` |

## 目录结构

```
can/
├── README.md
├── SKILL.md
├── config.json
├── config.example.json
├── scripts/
│   ├── can_scan.py
│   ├── can_monitor.py
│   ├── can_send.py
│   ├── can_log.py
│   ├── can_decode.py
│   └── can_stats.py
└── references/
    └── common_interfaces.json
```

## 支持的接口

| 接口 | 平台 | 备注 |
|------|------|------|
| `pcan` | Windows | 需安装 PEAK 驱动 |
| `vector` | Windows | 需安装 Vector XL Driver Library |
| `ixxat` | Windows | 需安装 IXXAT VCI 驱动 |
| `kvaser` | Windows / Linux | 需安装 Kvaser CANlib |
| `slcan` | Windows / Linux | 串口转 CAN，需 pyserial |
| `socketcan` | Linux | 内核原生支持 |
| `gs_usb` | Linux | candleLight / CANable 等 gs_usb 固件设备 |
| `virtual` | 全平台 | 虚拟总线，用于测试 |
