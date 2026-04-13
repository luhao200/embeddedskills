---
name: net
description: >-
  嵌入式网络调试工具，用于发现接口、抓包、分析 pcap/pcapng、做连通性测试、端口扫描和流量统计。
  当用户提到 Wireshark、tshark、Npcap、抓包、网络联调、端口扫描、连通性排查、pcap 分析、
  网络接口、ping 测试、traceroute、流量统计、Modbus TCP、EtherNet/IP 等网络协议调试时自动触发，
  也兼容 /net 显式调用。即使用户只是说"抓个包看看"、"扫一下端口"、"网络通不通"或"分析一下这个 pcap"，
  只要上下文涉及网络通信调试就应触发此 skill。
argument-hint: "[iface|capture|analyze|ping|scan|stats] ..."
---

# Net Debug Skill

嵌入式网络通信调试工具，统一封装接口发现、抓包、离线分析、连通性测试、端口扫描和流量统计能力。

## 脚本与配置路径

- 脚本目录: `<skill-dir>/scripts/`
- 配置文件: `<skill-dir>/config.json`
- 协议参考: `<skill-dir>/references/common_protocols.json`

## 依赖

- `tshark` (随 Wireshark 安装，需加入 PATH)
- `dumpcap` (随 Wireshark 安装)
- 可选: `capinfos`
- Windows 自带: `ipconfig`、`ping`、`tracert`、`netstat`、`arp`、`nslookup`
- Python 3.x (仅标准库)
- 抓包需要 Npcap 驱动，部分环境需管理员权限

## 配置文件

连接和采集参数统一从 `config.json` 读取，脚本不通过命令行接收这些连接参数。若配置缺少必要项或连接失败，询问用户并引导修改配置。

## 执行流程

1. 检查 `tshark` 是否可用
2. 读取 `config.json`，合并 interface / target / filter / duration / timeout 默认值
3. 若无子命令，默认执行 `iface`（列出网络接口）
4. 运行对应脚本并输出结构化 JSON 结果
5. 失败时优先提示权限、Npcap、过滤器、接口选择等问题

## 子命令

### iface — 列出网络接口

```bash
python <skill-dir>/scripts/net_iface.py [--filter <关键词>] [--tshark] [--json]
```

- `--tshark`: 同时显示 tshark 抓包接口索引映射
- `--filter`: 按关键词筛选接口
- 无副作用，可直接执行

### capture — 抓包

```bash
python <skill-dir>/scripts/net_capture.py [--output <文件路径>] [--format <pcapng|pcap>] [--decode-as <规则>] [--json]
```

- 接口、过滤器、时长从 config.json 读取
- `--output`: 保存抓包文件路径
- `--json`: 输出 JSON Lines 格式（基于 tshark -T ek）
- `--decode-as`: 自定义解码规则
- 默认格式 pcapng，参数完整后直接执行

### analyze — 分析 pcap 文件

```bash
python <skill-dir>/scripts/net_analyze.py <pcap_file> [--mode <summary|protocols|conversations|endpoints|io|anomalies|all>] [--filter <显示过滤器>] [--top <数量>] [--decode-as <规则>] [--export-fields <字段列表>] [--output <CSV路径>] [--json]
```

- 基于 tshark 和 capinfos 进行离线分析
- `--mode all` 输出全部分析维度
- 无副作用，可直接执行

### ping — 连通性测试

```bash
python <skill-dir>/scripts/net_ping.py [--tcp <端口>] [--count <次数>] [--traceroute] [--concurrent <线程数>] [--json]
```

- 目标从 config.json 读取
- `--tcp`: TCP 连通性测试（指定端口）
- `--traceroute`: 执行路由追踪
- 参数完整后直接执行

### scan — 端口扫描

```bash
python <skill-dir>/scripts/net_scan.py [--timeout <毫秒>] [--banner] [--concurrent <线程数>] [--json]
```

- 目标和端口范围从 config.json 读取
- `--banner`: 尝试获取服务 Banner
- 默认收敛到嵌入式常用端口集
- 参数完整后直接执行

### stats — 流量统计

```bash
python <skill-dir>/scripts/net_stats.py [--interval <秒>] [--mode <overview|protocol|endpoint|port>] [--json]
```

- 接口和时长从 config.json 读取
- 默认输出按时段汇总的 JSON
- 无副作用，可直接执行

## 输出格式

所有脚本输出统一的 JSON 结构:

```json
{
  "status": "ok",
  "action": "<子命令名>",
  "summary": "<简要描述>",
  "details": { ... }
}
```

错误时:

```json
{
  "status": "error",
  "action": "<子命令名>",
  "error": {
    "code": "<错误码>",
    "message": "<错误描述>"
  }
}
```

`capture --json` 输出 JSON Lines，进度信息写入 stderr。

## 交互策略

- 优先用 config.json 中的参数直接执行，不额外询问
- 连接失败时再询问用户并引导修改 config.json
- 未给扫描范围时默认收敛到单主机、小范围端口
- 结果中明确回显目标范围、过滤器和持续时间
- 抓包结果优先总结异常协议、重传、RST 等
- 抓包失败优先提示权限和 Npcap 问题

## 协议参考

需要查询嵌入式常用端口和协议映射时，读取 `references/common_protocols.json`。
