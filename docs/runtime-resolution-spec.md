# 运行环境读取统一规范

## 目的

本文档定义 embeddedskills 各个 skill 后续统一采用的运行环境读取规范，用于替代当前各 skill 分散且不一致的参数解析逻辑。

目标：

- 统一 `CLI / skill/config.json / .embeddedskills/config.json / .embeddedskills/state.json / 系统 PATH` 的职责和优先级
- 明确哪些参数允许从哪些层读取
- 要求脚本主动探测系统 `PATH`，而不是仅依赖命令执行失败后的被动报错
- 统一参数来源回显、自动写回和缺失参数处理

## 适用范围

适用于以下 skill：

- `can`
- `serial`
- `net`
- `gcc`
- `keil`
- `jlink`
- `openocd`
- `probe-rs`
- `workflow`

## 文件职责

### 1. CLI 显式参数

最高优先级。用户本次调用显式提供的参数必须覆盖其他一切来源。

### 2. `skill/config.json`

本机环境级配置，只存放当前机器相关且不应共享到工程仓库的内容。

允许存放：

- 工具可执行文件路径，如 `uv4_exe`、`cmake_exe`、`gdb_exe`
- 本机探针/抓包工具/串口工具默认路径
- 本机私有硬件参数，如本机默认探针序列号

不应存放：

- 工程路径
- 芯片型号、Target、board/interface/target
- 构建 preset、日志目录、产物路径

### 3. `<workspace>/.embeddedskills/config.json`

工程级共享配置。用于保存项目成员可共享的默认参数。

允许存放：

- 工程路径、Target、preset
- 芯片型号、接口类型、协议类型
- `workflow.preferred_*`
- 共享日志目录

不应存放：

- 机器相关的绝对工具路径
- 依赖当前 Windows 用户目录的路径

### 4. `<workspace>/.embeddedskills/state.json`

运行状态，不是配置真值源，只作为最近一次成功执行的回退来源。

允许存放：

- 最近一次成功构建/烧录/调试/观测使用的参数
- 最近一次构建产物路径
- 最近一次自动发现或自动补全得到的结果

不应用于：

- 长期保存工具路径
- 覆盖工程配置

### 5. 系统 `PATH`

用于主动发现工具命令，不属于配置文件层，但属于正式解析层级。

适用对象：

- `cmake`
- `probe-rs`
- `openocd`
- `arm-none-eabi-gdb`
- `JLink.exe`、`JLinkGDBServerCL.exe`
- `tshark.exe`、`capinfos.exe`
- 其他可执行工具

## 统一解析模型

不是所有参数都走完全相同的顺序。统一规范按参数类型定义可用来源。

### A. 可执行文件/命令参数

如：

- `exe`
- `uv4_exe`
- `cmake_exe`
- `gdb_exe`
- `gdbserver_exe`
- `tshark_exe`

统一优先级：

1. CLI 显式参数
2. `skill/config.json`
3. 系统 `PATH`
4. 内建命令名默认值
5. 报错

规则：

- 不从 `.embeddedskills/config.json` 读取工具绝对路径
- 不从 `state.json` 回退工具路径
- 若 `PATH` 命中多个候选，优先使用 `shutil.which()` 返回的首个结果

### B. 工程/硬件配置参数

如：

- `project`
- `target`
- `preset`
- `device`
- `chip`
- `board`
- `interface`
- `transport`
- `protocol`
- `speed`
- `adapter_speed`
- `connect_under_reset`

统一优先级：

1. CLI 显式参数
2. `.embeddedskills/config.json`
3. `.embeddedskills/state.json`
4. 自动发现
5. 内建默认值
6. 询问用户或报错

规则：

- 不从 `skill/config.json` 读取这类工程真值参数
- `state.json` 仅作为最近一次成功结果的回退
- 自动发现只能用于“可枚举且歧义可控”的参数，如单一工程、单一串口、单一 CAN 接口

### C. 产物/输入文件路径参数

如：

- `elf`
- `file`
- `flash_file`
- `debug_file`

统一优先级：

1. CLI 显式参数
2. `.embeddedskills/config.json`
3. `.embeddedskills/state.json`
4. 工作区搜索
5. 报错

规则：

- 允许从 `state.json` 回退上次成功构建产物
- 工作区搜索必须可解释，优先搜索最近一次构建目录或约定目录，不允许无界递归猜测

### D. 运行时端口/日志/观测参数

如：

- `gdb_port`
- `telnet_port`
- `rtt_port`
- `log_dir`
- `capture_format`

统一优先级：

1. CLI 显式参数
2. `.embeddedskills/config.json`
3. `skill/config.json`
4. `.embeddedskills/state.json`
5. 内建默认值

规则：

- `log_dir` 属于工程级优先参数，优先取工程配置
- 端口类参数允许环境级提供机器默认值

### E. workflow 兼容覆盖文件

仅 `workflow` 保留 `--config` 兼容入口。

优先级：

1. CLI 显式参数
2. `--config` 指向的兼容配置文件
3. `.embeddedskills/config.json`
4. 自动发现
5. 报错

说明：

- 该层仅用于兼容旧用法，不作为其他 skill 的通用机制

## 系统 PATH 主动探测规范

所有需要外部工具的 skill 必须主动探测系统 `PATH`。

### 探测要求

1. 优先使用 Python `shutil.which()`
2. Windows 下按 `PATHEXT` 规则解析 `.exe`、`.cmd`、`.bat`
3. 允许为同一工具定义候选命令名列表
4. 命中后应记录绝对路径
5. 未命中时再进入默认值或报错分支

### 候选命令示例

- `cmake`: `["cmake.exe", "cmake"]`
- `probe-rs`: `["probe-rs.exe", "probe-rs"]`
- `openocd`: `["openocd.exe", "openocd"]`
- `arm-none-eabi-gdb`: `["arm-none-eabi-gdb.exe", "arm-none-eabi-gdb"]`
- `JLink.exe`: `["JLink.exe"]`
- `JLinkGDBServerCL.exe`: `["JLinkGDBServerCL.exe"]`
- `tshark`: `["tshark.exe", "tshark"]`

### 来源标记

命中 `PATH` 时，`parameter_sources` 统一记为：

- `path:cmake.exe`
- `path:probe-rs.exe`
- `path:arm-none-eabi-gdb.exe`

不要只记录成模糊的 `path`。

## 自动写回规范

### 写回 `.embeddedskills/config.json`

仅允许写回“工程级已确认参数”：

- 单一候选自动发现成功后的工程参数
- 用户执行成功后确认有效的 `device/chip/interface/target/preset/project`
- `workflow.preferred_*`

### 写回 `.embeddedskills/state.json`

写回最近一次成功运行记录：

- `last_build`
- `last_flash`
- `last_debug`
- `last_observe`
- 技术上可复用的产物路径

### 不自动写回 `skill/config.json`

即使工具通过 `PATH` 成功发现，也不自动写回 `skill/config.json`。

原因：

- `skill/config.json` 是本机显式配置，不应被一次临时命中自动污染
- 如果未来需要持久化 PATH 命中结果，应由用户显式确认

## 缺失参数处理

### 允许自动发现

仅限以下场景：

- 扫描后只有一个工程
- 扫描后只有一个串口
- 扫描后只有一个 CAN 接口
- 工作区中只有一个明显匹配的构建产物

### 不允许自动猜测

以下参数缺失时必须报错或要求用户明确指定：

- 多个候选中的 `device`
- 多个候选中的探针序列号
- 多个候选中的 `board/interface/target`
- `.bin` 烧录地址

## 统一实现建议

后续应收敛为一个通用 resolver，至少支持：

- `cli_value`
- `project_config`
- `local_config`
- `state_record`
- `path_candidates`
- `default`
- `required`
- `normalize_as_path`
- `source_policy`

其中 `source_policy` 用于声明当前参数属于哪一类：

- `tool_exe`
- `project_param`
- `artifact_path`
- `runtime_option`

## 与当前仓库的差异

本规范生效后，以下行为需要统一修正：

- `can / serial / net` 的 helper 文档与实际调用不一致
- `jlink` 和 `openocd` 的 `SKILL.md` 前后存在两套优先级描述
- `openocd_telnet.py` 需要与其他 `openocd_*` 脚本对齐
- `probe-rs` 文档需要补充 `PATH` 主动探测层
- `README.md` 与 `docs/getting-started.md` 的“三层配置”描述需要升级为包含 `PATH` 的统一模型

## 统一对外表述

面向用户时，推荐统一表述为：

> 参数优先级按参数类型解析。工程参数优先读取 CLI 和 `.embeddedskills/config.json`，工具路径优先读取 CLI、`skill/config.json` 和系统 `PATH`，运行历史只作为 `state.json` 回退来源。
