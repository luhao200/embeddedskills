# ssh

Claude Code skill，用于 SSH 服务器与 Linux 开发板操作：OpenSSH 配置管理、远程命令、文件上传下载、跳板机和本地端口转发。

## 功能

- 读取、查询和新增 `~/.ssh/config` 中的 `Host` 别名
- 通过 Host 别名执行远程命令，并返回结构化 JSON
- 通过固定只读命令探测远端系统、运行时和 GPU 概况
- 使用 `scp` 上传和下载文件
- 建立本地端口转发，支持访问远端服务
- 支持 `ProxyJump` 跳板机配置
- 首次连接可信设备时，可显式接受新主机指纹

## 环境要求

- Python 3.x（仅标准库，无额外 Python 依赖）
- OpenSSH 客户端：`ssh`、`scp`、`ssh-keygen`
- 可选：已配置 SSH 密钥，推荐使用 `IdentityFile`

Windows 10/11 通常已内置 OpenSSH 客户端；如果命令不可用，可在“可选功能”中安装 OpenSSH Client。

## 配置

ssh skill 不维护独立服务器数据库，唯一服务器清单是标准 OpenSSH 配置：

```text
~/.ssh/config
```

推荐使用 Host 别名管理设备：

```ssh-config
# description: Linux 开发板
# tags: embedded,linux,dev-board
# location: lab
Host 1380-P904
    HostName 192.168.137.76
    User root
    Port 22
    IdentityFile ~/.ssh/id_ed25519
```

跳板机示例：

```ssh-config
Host bastion
    HostName bastion.example.com
    User root
    IdentityFile ~/.ssh/id_ed25519

Host internal-dev
    HostName 10.0.1.20
    User root
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump bastion
```

允许保留以下注释元数据：

| 字段 | 说明 |
|------|------|
| `description` | 设备或服务器说明 |
| `aliases` | 用户习惯称呼，逗号分隔；可用于精确解析 |
| `groups` | 逻辑环境或主机组，逗号分隔；允许多台主机共享 |
| `tags` | 逗号分隔的标签 |
| `location` | 位置或环境 |

不要在 `~/.ssh/config` 中写入真实密码、Token、私钥内容或其他敏感信息。

## 常用命令

命令示例均以当前 skill 目录为基准。

### 列出服务器

```bash
python scripts/ssh_config.py list
```

### 查找服务器

```bash
python scripts/ssh_config.py find <关键词>
python scripts/ssh_config.py find <环境关键词> <能力关键词>
```

多个关键词采用 AND 语义。例如，先用 `groups` 标记测试环境成员，再用
`tags` 标记硬件能力后，`find 测试环境 4090D` 只返回同时满足两个条件的主机。
`search` 是 `find` 的等价命令。

### 确定唯一服务器

```bash
python scripts/ssh_config.py resolve <关键词...>
```

`resolve` 只有在结果唯一时才成功。没有匹配返回退出码 1；存在多个候选时
返回退出码 2 和候选列表，避免 AI 在共享环境名称下擅自选择主机。OpenSSH
`Host` 的精确别名优先于模糊匹配。

### 验证别名解析

```bash
python scripts/ssh_config.py show <别名>
```

### 新增服务器

写入前脚本会自动备份 `~/.ssh/config`：

```bash
python scripts/ssh_config.py add <别名> --host <IP或域名> --user <用户> --port 22 --key ~/.ssh/id_ed25519
```

常用可选参数：

```bash
--description "说明"
--aliases "常用名称1,常用名称2"
--groups "环境1,环境2"
--tags tag1,tag2
--location "位置"
--proxy-jump <跳板机别名>
```

### 执行远程命令

```bash
python scripts/ssh_exec.py <别名> "uname -a" --timeout 30
```

脚本输出 JSON，包含 `success`、`exit_code`、`stdout`、`stderr`。

### 探测远端环境

```bash
python scripts/ssh_probe.py <别名>
```

该命令只执行内置的只读探测，不接受任意远程命令。结果以 JSON 返回，包含
操作系统、架构、内核、CPU 数量、内存、Python、Node.js、GPU 和容器环境等
信息。连接默认使用非交互认证、禁止转发，并要求主机指纹已经可信。
为避免 SSH 配置在本机执行任意命令，探测入口不会继承 `ProxyCommand`；需要
代理命令的目标应使用常规 SSH 脚本并单独确认。

执行前预览固定命令：

```bash
python scripts/ssh_probe.py <别名> --dry-run
```

### 上传文件

```bash
python scripts/ssh_transfer.py upload <别名> "<本地路径>" "<远程路径>"
```

### 下载文件

```bash
python scripts/ssh_transfer.py download <别名> "<远程路径>" "<本地路径>"
```

### 建立本地端口转发

```bash
python scripts/ssh_tunnel.py <别名> --local-port <本地端口> --remote-host 127.0.0.1 --remote-port <远程端口>
```

隧道命令会前台运行。需要后台长期保持时，先确认停止方式。

## 首次连接主机指纹

`ssh_exec.py`、`ssh_transfer.py`、`ssh_tunnel.py` 均支持：

```bash
--accept-new-host-key
--known-hosts-file <临时known_hosts路径>
```

- `--accept-new-host-key`：确认设备可信时，允许 OpenSSH 接受新的主机指纹。
- `--known-hosts-file`：指定 `known_hosts` 文件。调试时可使用临时文件，避免污染全局 `~/.ssh/known_hosts`。

示例：

```bash
python scripts/ssh_exec.py 1380-P904 "echo SSH_OK && uname -m" --accept-new-host-key
```

## 操作边界

- 查询类任务可以直接执行。
- 新增或修改 `~/.ssh/config` 前，脚本必须创建备份。
- 删除配置、覆盖远程文件、部署、批量执行、端口转发等有风险操作，先确认。
- 执行远程命令时优先只读检查；涉及重启、删除、覆盖、安装、升级时先确认。
- 如果脚本失败，保留真实 stderr，不要吞掉错误。

## 故障排查

优先检查：

1. `python scripts/ssh_config.py show <别名>`
2. `ssh -G <别名>` 是否能解析 HostName/User/Port
3. `ssh-keygen -F <HostName>` 是否已有主机指纹
4. 密钥文件是否存在，权限是否合适
5. `ProxyJump` 别名是否也在 `~/.ssh/config`
6. 网络是否可达，端口是否开放
7. 首次连接是否需要显式追加 `--accept-new-host-key`
