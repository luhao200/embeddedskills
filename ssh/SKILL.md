---
name: ssh
description: SSH/服务器操作助手。用于远程服务器、user@host、SSH 配置、上传下载、部署、跳板机、隧道、端口转发、服务器命令执行等任务；以 ~/.ssh/config 的 Host 别名为唯一服务器清单，优先密钥认证，通过本 skill 的 Python 脚本封装 OpenSSH 操作。
---

# SSH Skill

## 定位

这是一个轻量 SSH 操作网关。它不维护独立服务器数据库，默认只读取和写入标准 OpenSSH 配置：

```text
~/.ssh/config
```

核心原则：

- 使用 `Host` 别名标识服务器，不直接记忆 IP/密码。
- 优先密钥认证和 OpenSSH 原生命令。
- 通过本 skill 的 `scripts/` 脚本执行 SSH、SCP、配置检查和隧道操作。
- 写入 `~/.ssh/config` 前必须自动备份。
- 不鼓励密码落盘；如必须使用密码，优先让 OpenSSH 交互提示或由用户自行配置安全凭据。

## 何时触发

当用户提到以下任务时使用本 skill：

- SSH、远程服务器、服务器 IP/主机名、`user@host`
- 登录、执行远程命令、检查服务器状态
- 上传、下载、部署、迁移文件
- 跳板机、`ProxyJump`、内网访问
- 隧道、端口转发、数据库连接
- 配置 `~/.ssh/config`、新增/查找服务器别名

不要用于本机 `localhost`、当前目录、本地文件操作或普通网络概念解释。

## 脚本入口

优先从当前 skill 目录调用脚本。脚本目录为：

```text
scripts/
```

命令示例均以当前 skill 目录为基准。

## 常用命令

`ssh_exec.py`、`ssh_transfer.py`、`ssh_tunnel.py` 均支持：

```bash
--accept-new-host-key
--known-hosts-file <临时known_hosts路径>
```

首次连接已确认可信的新开发板时，可显式追加 `--accept-new-host-key`。测试时如不想写入全局 `known_hosts`，可追加 `--known-hosts-file <临时known_hosts路径>`。

### 列出服务器

```bash
python scripts/ssh_config.py list
```

### 查找服务器

```bash
python scripts/ssh_config.py find <关键词>
python scripts/ssh_config.py find <环境关键词> <能力关键词>
```

多个关键词使用 AND 匹配。先用环境组缩小范围，再用设备别名或标签确定目标。
`search` 是 `find` 的等价命令。

### 确定唯一服务器

```bash
python scripts/ssh_config.py resolve <关键词...>
```

仅在结果唯一时继续。退出码 2 表示存在多个候选；必须向用户确认，不能自动
选择第一项。精确的 OpenSSH `Host` 别名优先。

### 验证别名解析

```bash
python scripts/ssh_config.py show <别名>
```

### 新增服务器

写入前脚本会自动备份 `~/.ssh/config`：

```bash
python scripts/ssh_config.py add <别名> --host <IP或域名> --user <用户> --port 22 --key ~/.ssh/id_ed25519
```

可选：

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
python scripts/ssh_exec.py <别名> "命令" --timeout 30
```

脚本输出 JSON，包含 `success`、`exit_code`、`stdout`、`stderr`。

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

隧道命令会前台运行。需要后台长期保持时，先向用户说明影响和停止方式。

## 配置格式

推荐配置：

```ssh-config
# description: 开发板
# aliases: 测试板,主控板
# groups: 实验室测试环境
# tags: embedded,linux
# location: lab
Host 1380-P904
    HostName 192.168.137.76
    User root
    Port 22
    IdentityFile ~/.ssh/id_ed25519
```

跳板机：

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

允许保留注释元数据：

- `description`
- `aliases`
- `groups`
- `tags`
- `location`

不要在配置中写入真实密码、Token、私钥内容或其他敏感信息。

## 操作规则

- 查询类任务可以直接执行。
- 新增或修改 `~/.ssh/config` 前，脚本必须创建备份。
- 删除配置、覆盖远程文件、部署、批量执行、端口转发等有风险操作，先向用户确认。
- 不直接运行裸 `ssh`/`scp`，优先使用本 skill 的脚本；只有在脚本不可用或用户明确请求时，才说明原因并使用回退命令。
- 不修改 Git、系统服务、防火墙、远程生产环境配置，除非用户明确要求。
- 执行远程命令时优先只读检查；涉及重启、删除、覆盖、安装、升级时先确认。
- 输出给用户时说明目标别名、实际 HostName、执行命令、关键结果和失败原因。

## 故障排查

优先检查：

1. `python scripts/ssh_config.py show <别名>`
2. `ssh -G <别名>` 是否能解析 HostName/User/Port
3. 密钥文件是否存在，权限是否合适
4. `ProxyJump` 别名是否也在 `~/.ssh/config`
5. 网络是否可达，端口是否开放
6. 首次连接是否需要显式追加 `--accept-new-host-key`

如果脚本失败，保留真实 stderr，不要吞掉错误。
