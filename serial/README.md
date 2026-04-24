# serial

Claude Code skill，用于嵌入式串口调试：端口扫描、实时监控、数据发送、Hex 查看和日志记录。

## 功能

- 扫描系统可用串口
- 实时监控串口文本输出（支持正则过滤、时间戳）
- 发送文本或 Hex 数据（支持 AT 命令调试）
- 二进制流 Hex 查看
- 串口日志保存（text / csv / json 格式）

## 环境要求

- Python 3.x
- [pyserial](https://pypi.org/project/pyserial/) — `pip install pyserial`
- [socat](http://www.dest-unreach.org/socat/) — `apt install socat` / `pacman -S socat`（多路复用功能需要）
- USB 转串口芯片驱动（CH340、CP2102、FT232 等，按硬件安装对应驱动）

## 配置

### 环境级配置 (`config.json`)

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

| 字段 | 必填 | 说明 |
|------|------|------|
| `port` | 否 | 串口号（如 `COM3`），为空时自动扫描 |
| `baudrate` | 否 | 波特率，默认 115200 |
| `bytesize` | 否 | 数据位，默认 8 |
| `parity` | 否 | 校验位：`none` / `even` / `odd` / `mark` / `space` |
| `stopbits` | 否 | 停止位：`1` / `1.5` / `2` |
| `encoding` | 否 | 文本编码，默认 `utf-8` |
| `timeout_sec` | 否 | 读写超时秒数，默认 1.0 |
| `log_dir` | 否 | 日志输出目录，默认 `.embeddedskills/logs/serial` |

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
