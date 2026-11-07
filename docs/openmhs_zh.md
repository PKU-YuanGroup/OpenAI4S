# OpenMHS 集成（实验性）

[English](openmhs.md)

[OpenMHS](https://github.com/PKU-YuanGroup/OpenMHS) 是独立的开源项目，
与 Anthropic 无关联，也未获其背书。它的 OpenAI4S 参考宿主通过 MCP
连接科研设备和模拟器。本集成**处于实验阶段，默认关闭**。

OpenAI4S 只包含可选路由钩子，不包含 OpenMHS SDK 或参考宿主。它们保留在
OpenMHS 仓库的 `MHS/sdk`（发行包 `openmhs`，导入名 `mhs`）和
`integration/openai4s/openai4s_mhs`（参考宿主）中。OpenAI4S 核心仍只依赖
Python 标准库；不安装这两个包也能正常运行。

## 从 OpenMHS 源码安装

需要 Python 3.10+，使用运行 OpenAI4S 的同一个解释器和环境。在 OpenAI4S
检出目录中，另行获取 OpenMHS 仓库，将两个源码包加入导入路径：

```bash
git clone https://github.com/PKU-YuanGroup/OpenMHS.git ../OpenMHS
export OPENMHS_CHECKOUT="$(cd ../OpenMHS && pwd)"
export PYTHONPATH="$OPENMHS_CHECKOUT/MHS/sdk/src:$OPENMHS_CHECKOUT/integration/openai4s:$PWD${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "import openai4s, openai4s_mhs, mhs; print('imports ready')"
python3 -m openai4s_mhs status
```

这种源码安装不需要第三方依赖。启动 OpenAI4S daemon 时也要保留相同的
`PYTHONPATH`；只让配置 CLI 能导入这些包，不会让已运行的 daemon 也能导入。
参考宿主目前从 `integration/openai4s` 运行，不要把它复制到 OpenAI4S 包中。

如果希望把 SDK 安装到所选环境，OpenMHS 文档要求先在其仓库根目录执行：

```bash
python3 MHS/sdk/tools/sync_data.py
python3 -m pip install ./MHS/sdk
```

即使安装了 SDK，参考宿主仍需要将 `integration/openai4s` 加入 `PYTHONPATH`。
当前 OpenAI4S 已包含核心路由和自动安装钩子，无需再应用 OpenMHS 针对旧版
OpenAI4S 的历史补丁。详情参见上游的
[参考宿主指南](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/README.md)
和[设计契约](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/DESIGN.md)。

## 配置、启用与停用

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
  结果必须先对账，才能重试。
- 只有明确绑定设备的 `mhs__estop`（或 Host 动作 `mhs_emergency_stop`）跳过
  普通审批提示。connector 启用状态、委派子代理的权限限制、端点与设备绑定、
  参考宿主的安全策略仍然生效。停止动作以外部写入记录审计，返回内容与其他
  MCP 输出一样接受注入筛查。
- 复位和许可属于独立的人工操作者路由，不能通过代理 connector 执行。
  硬件访问另有默认关闭的开关。软件停止不能替代安全认证的硬件急停，模拟证据
  也不代表硬件验证。
- 如果 MHS connector 的参考宿主无法导入，调用会被拒绝，避免在缺少命令日志和
  宿主账本的情况下执行命令。其他 connector 保留原来的 MCP 行为。

命令日志、宿主账本和端点证据的实现由 OpenMHS 维护。OpenAI4S 测试覆盖可选
路由和权限边界；OpenMHS 集成套件覆盖参考宿主的端到端行为。
