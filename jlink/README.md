# jlink

Claude Code skill，通过 J-Link 探针进行嵌入式设备的固件烧录、内存读写、寄存器查看、RTT/SWO 日志读取和在线调试。

## 功能

- 探针与目标连通性探测
- 固件烧录（.hex / .bin / .elf）
- 内存读写、寄存器查看、目标复位
- RTT 日志实时读取
- SWO 事件流包装（通过外部 viewer）
- 在线调试：暂停/恢复/单步/断点运行
- GDB 源码级调试：调用栈、局部变量查看

## 环境要求

- [SEGGER J-Link Software](https://www.segger.com/downloads/jlink/) — 提供 JLink.exe、JLinkGDBServerCL.exe、JLinkRTTClient.exe
- Python 3.x（仅标准库，无额外依赖）
- GDB 调试需要 `arm-none-eabi-gdb`（随 [Arm GNU Toolchain](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain) 安装）

## 配置

复制 `config.example.json` 为 `config.json`，根据实际安装路径修改：

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

| 字段 | 必填 | 说明 |
|------|------|------|
| `exe` | 是 | JLink.exe 完整路径 |
| `gdbserver_exe` | 否 | JLinkGDBServerCL.exe 路径，RTT 和 GDB 调试需要 |
| `rtt_exe` | 否 | JLinkRTTClient.exe 路径，RTT 需要 |
| `gdb_exe` | 否 | arm-none-eabi-gdb 路径，GDB 源码级调试需要 |
| `default_elf` | 否 | 默认 ELF 路径；为空时优先读取 `.embeddedskills/state.json` |
| `default_device` | 否 | 默认芯片型号（如 `STM32F407VG`），为空时需手动指定 |
| `default_interface` | 否 | 调试接口：`SWD` 或 `JTAG`，默认 `SWD` |
| `default_speed` | 否 | 调试速率 kHz，默认 `4000` |
| `serial_no` | 否 | 探针序列号，多探针场景使用 |
| `rtt_telnet_port` | 否 | RTT 端口，`0` 使用工具默认值 |
| `swo_command` | 否 | 外部 SWO viewer 的完整命令数组，供 `jlink_swo.py` 包装 |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |
