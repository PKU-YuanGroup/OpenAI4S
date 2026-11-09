# OpenMHS 集成

[English](openmhs.md)

[OpenMHS](https://mhs.openai4s.org) 是开源的 Model Hardware Standard。
OpenAI4S 参考宿主通过 MCP 连接科研设备和模拟器，配置端点后即可启用。

## 快速开始

```bash
git clone --recursive https://github.com/PKU-YuanGroup/OpenAI4S.git
cd OpenAI4S && ./setup.sh && source .venv/bin/activate
openai4s lab add labs/tiagocoutinho__julabo   # 任意设备 id，见 https://mhs.openai4s.org/market/
openai4s serve                               # 打开 Notebook 旁边的“实验室”标签页
```

`--recursive` 会拉取 `third_party/OpenMHS` 子模块，固定在本版本测试过的 OpenMHS
发行版。OpenAI4S 会自动找到它：不用设置 `PYTHONPATH`，也不用手写设置文件。
`openai4s lab add` 以 fake 后端安装设备、启动实验室（3 台内置演示设备加上你安装的
设备）、注册连接器，并为这些模拟设备预授权。

已经不带 `--recursive` 克隆过？运行 `git submodule update --init third_party/OpenMHS`。
用 `pip` 安装的 OpenAI4S？`openai4s lab setup` 会把同一个固定版本的 OpenMHS 下载到
`~/.openai4s/openmhs`（实验室面板里的 **一键准备 OpenMHS** 按钮也一样）。在工作台里，
其余都是一次点击：实验室标签页 → **设备市场** → **安装**。

## 设备市场：746 台设备

[OpenMHS 市场](https://mhs.openai4s.org/market/) 收录了所有在 fake 后端通过契约检查的
驱动包：299 个硬件驱动（以 fake 后端提供，不需要真实仪器）和 447 个仿真环境。每个条目都有
介绍、可读取/可设置/可执行的内容、上游仓库和许可证。

```bash
openai4s lab search microscope        # 查找设备
openai4s lab show julabo              # 查看介绍
openai4s lab add julabo               # 安装（默认 MCP 连接器）
openai4s lab add gym-pusht --via mhs  # 以 MHS 原生工具安装
openai4s lab list                     # 已安装的设备
openai4s lab status                   # 全部设备、接入方式与状态
openai4s lab remove julabo
openai4s lab up | down | reset        # 启动、停止、清空证据（保留已装设备）
```

“在 fake 后端通过契约检查”不是硬件资质认证。驱动真实硬件仍是实验室之外、由操作员
明确做出的决定。

## MHS 还是 MCP：两种智能体接入面

每台实验室设备都通过两种接入面之一被调用，实验室面板也按此分组：

| 接入面 | 智能体如何调用 | 线路 | 适合 |
| --- | --- | --- | --- |
| **MHS**（原生） | `mhs_read`、`mhs_command`、`mhs_safety` 工具；OpenAI4S 进程内的 MHSHost | HTTP 上的 MHS JSON-RPC，端点由实验室启动（端口从 7420 起） | 路径最短；一个工具调用所有设备 |
| **MCP**（连接器） | `call_mcp_tool(server="mhs-<id>", ...)`；每台设备一行连接器 | MCP 投影 `mhs.mcp`，通过 stdio 拉起驱动（或访问 HTTP 端点） | 任何 MCP 宿主；受治理路由的日志、主机账本与证据 |

安装时用 `--via mhs|mcp` 或市场里的选择框为每台设备选一种。两者都经过同一个端点，
限值、联锁、急停和回执完全一致。内置反应器用 MHS；内置酶抑制工作站（HTTP）和 xArm
（stdio）用 MCP。

## 进阶：使用自己的 OpenMHS 检出

OpenAI4S 依次在 `$OPENAI4S_OPENMHS`、`third_party/OpenMHS` 子模块、`<数据目录>/openmhs`
中查找 OpenMHS，并把它的两个源码包（`MHS/sdk/src`，发行包 `openmhs`，导入名 `mhs`；
参考宿主 `integration/openai4s/openai4s_mhs`）加入 `sys.path` 和它所启动进程的
`PYTHONPATH`。如果已有 `PYTHONPATH` 或已安装的发行包提供了这两个包，则以它们为准。
设置 `OPENAI4S_OPENMHS=off` 可关闭查找，核心将与没有 OpenMHS 时完全一致。OpenAI4S
核心仍只依赖 Python 标准库；不安装这两个包也能正常运行。

```bash
export OPENAI4S_OPENMHS=/path/to/OpenMHS
python3 -c "import openai4s, openai4s_mhs, mhs; print('imports ready')"
python3 -m openai4s_mhs status
```

本仓库已包含核心路由与自动安装钩子；不要把 OpenMHS 的历史补丁再次应用到旧的 OpenAI4S
提交上。参见上游的[参考宿主指南](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/README.md)
和[设计约定](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/DESIGN.md)。

## 进阶：配置自己的端点

创建 `~/.openai4s/mhs.json`，或通过 `MHS_CONFIG` 指定其他文件。
先使用模拟端点，保持硬件访问关闭：

```json
{
  "enabled": false,
  "allow_hardware": false,
  "endpoints": [
    {"id": "lab", "url": "http://127.0.0.1:7401", "label": "simulated lab"}
  ]
}
```

在另一个终端中使用相同的 Python 环境和源码路径，启动 OpenMHS 模拟实验室：

```bash
python3 -m mhs.reference lab --port 7401
```

然后由操作者明确启用集成：

```bash
python3 -m openai4s_mhs mcp-config --json  # preview one disabled row per endpoint
python3 -m openai4s_mhs enable
python3 -m openai4s_mhs status
./start.sh
```

`enable` 持久化开关，并把启用的 connector 行同步到 OpenAI4S 的 Store。
如果使用了自定义数据目录，CLI 与 daemon 必须使用相同的 `OPENAI4S_DATA_DIR`。
集成配置文件的位置由 `MHS_CONFIG` 单独指定。对于已运行的 daemon，使用上述
源码路径重启，或重新加载 connector 配置，工具才会生效。每条 connector
通过配置文件启动 `mhs.mcp`；由于 MCP 子进程不继承 daemon 的 `PYTHONPATH`，
生成的行会在自身环境中声明 SDK 源码路径。

停用方式：

```bash
unset MHS_ENABLED
python3 -m openai4s_mhs disable
python3 -m openai4s_mhs status
```

重启 daemon，以移除该进程中已经注册的原生开发工具。`disable` 持久化
`enabled: false` 并关闭 MCP 行，但不会取消正在执行的设备操作。
`MHS_ENABLED=1` 是开发用的进程级覆盖项，优先于持久化开关；停用时也要从
daemon 环境中移除。导入 `openai4s` 会调用可选参考宿主的幂等 `install()`
钩子，它仅在启用时注册原生 `mhs_*` 开发工具。关闭时不注册工具，也不连接端点。

## 受治理路由的约束

- 普通能力调用仍需要审批。外部参考宿主在发送命令前持久化意图，将结果追加到
  宿主账本，并通过同一 MCP 传输记录经校验的端点证据。UNKNOWN 或仍在执行的
  结果必须先对账，才能重试。普通调用必须先成功发现工具；发现失败时不会回退到
  不记录命令日志的直接调用。
- 只有明确绑定设备的 `mhs__estop`（或 Host 动作 `mhs_emergency_stop`）跳过
  普通审批提示。connector 启用状态、持久化的 MCP 拒绝规则、委派子代理的权限限制、
  端点与设备绑定、参考宿主的安全策略仍然生效。停止动作以外部写入记录审计，返回内容与其他
  MCP 输出一样接受注入筛查。
- 复位和许可属于独立的人工操作者路由，不能通过代理 connector 执行。
  硬件访问另有默认关闭的开关。软件停止不能替代安全认证的硬件急停，模拟证据
  也不代表硬件验证。
- 如果 MHS connector 的参考宿主无法导入，调用会被拒绝，避免在缺少命令日志和
  宿主账本的情况下执行命令。其他 connector 保留原来的 MCP 行为。

命令日志、宿主账本和端点证据的实现由 OpenMHS 维护。OpenAI4S 测试覆盖可选
路由和权限边界；OpenMHS 集成套件覆盖参考宿主的端到端行为。

## 模拟实验室与“实验室”面板

`openai4s/mhs_lab` 是基于本集成的可选模拟实验室，工作台在 Notebook 旁的 **实验室**
标签页中展示它。它始终带 3 台内置设备，分别走不同的 OpenMHS 接入路径，另外加上你从
市场安装的任意设备：

| 设备 | 接入面 | 线路 |
| --- | --- | --- |
| AS-96 酶抑制分析工作站（新的模拟仪器） | MCP 连接器 `mhs-assay`，受治理宿主路由 | HTTP JSON-RPC 端点 |
| FR-10 连续流反应器 + 在线 HPLC（新的模拟仪器） | MHS：原生 `mhs_*` 工具（不经 MCP） | HTTP JSON-RPC 端点 |
| UFACTORY xArm 7（由 xArm-Python-SDK 适配的 OpenMHS 注册表驱动，fake 后端） | MCP 连接器 `mhs-xarm`，受治理宿主路由 | stdio 驱动进程 |
| 任意 OpenMHS 市场包（`openai4s lab add <id>`） | MCP 连接器 `mhs-<id>` 或 MHS 原生工具 | stdio，或端口从 7420 起的 HTTP 端点 |

内核 Cell 保持沙箱隔离：设备指令是控制面的工具调用，分析在 Cell 中进行。
`openai4s lab up`（或面板里的 **启动实验室**，或第一次 `openai4s lab add`）会启动端点，
并在 `<数据目录>/mhs-lab/` 下写入：`mhs.json`（原生工具的设置；当你没有自己的
`~/.openai4s/mhs.json` 时自动选用）、`mhs-connectors.json`（连接器行的 `--config`）、
`devices.json`（已安装的设备）、`lab.json` 和 `LAB-CARD.md`（确切的工具名、参数和限值；
operator reference). The project stores fixed discovery guidance; live driver metadata
is read through screened tool results. Starting or installing the lab adds no
global allow rules and removes legacy preview grants. Commands follow normal
host permissions. Children do not inherit provider keys. `openai4s lab reset`
clears lab state; `openai4s lab down` stops endpoints. After reboot, an operator
can restore a previously running lab; a team member's status poll cannot restart it.


实验室面板读取每个端点持久化的证据链（`--state-dir`），因此所有接入路径的回执都出现在
同一条实时流里，并附有由回执输出生成的图表和离线证据链校验。安装、启动/停止、急停和
操作员复位按钮都是人的操作，由 `/api/v1/mhs-lab/*` 路由提供；任何工具都无法触达。复位
只释放锁存；之后由设备声明的重新使能动作恢复运动。
