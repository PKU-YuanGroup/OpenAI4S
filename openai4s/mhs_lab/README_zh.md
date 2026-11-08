# openai4s/mhs_lab

[English](README.md)

可选的 OpenMHS 模拟实验室：仪器、实验室管理进程、设备市场，以及工作台"实验室"面板展示的数据。它需要 OpenMHS（SDK `mhs` 与参考主机 `openai4s_mhs`）；[`openmhs.py`](openmhs.py) 会在 `third_party/OpenMHS` 子模块（或 `openai4s lab setup` 下载的固定版本）中找到它，无需设置 `PYTHONPATH`。核心只导入这个查找器（不联网，也不导入 OpenMHS 本身）；`OPENAI4S_OPENMHS=off` 可关闭它。这里没有任何东西控制硬件：仪器是模拟器，其余设备都运行在 fake 后端上。

实验室始终带三台内置设备，分别走三种 OpenMHS 接入路径：酶抑制工作站经 MCP 连接器 `mhs-assay`（HTTP 端点）；流动反应器经原生 `mhs_*` 工具（HTTP 上的 MHS JSON-RPC，不经 MCP）；xArm 7 经 MCP 连接器 `mhs-xarm`（stdio 驱动进程）。OpenMHS 市场的 746 个包都可以加入（`openai4s lab add <id>` 或实验室面板里的设备市场），每台可任选一种接入：**MHS**（原生工具，HTTP 端点，端口从 7420 起）或 **MCP**（连接器行，由投影进程通过 stdio 拉起驱动）。内核 Cell 保持沙箱隔离，设备指令是控制面的工具调用。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 包说明、`INSTRUMENTS_DIR`、`lab_dir()`（`OPENAI4S_MHS_LAB_DIR`，否则 `<数据目录>/mhs-lab`）。 |
| [`openmhs.py`](openmhs.py) | 查找 OpenMHS 检出（`$OPENAI4S_OPENMHS`、`third_party/OpenMHS` 子模块、`<数据目录>/openmhs`），把源码加入 `sys.path`/`PYTHONPATH`，在操作员没有自己的设置时选用实验室的 `mhs.json`，并按需下载固定版本。 |
| [`supervisor.py`](supervisor.py) | 启停端点（`--state-dir`），安装/卸载市场设备（`devices.json`），写两份设置文档（原生工具用的 `mhs.json`、连接器行用的 `mhs-connectors.json`），启用连接器行，为模拟设备预授权，并生成设备卡。 |
| [`cli.py`](cli.py) | `openai4s lab add\|remove\|search\|show\|list\|status\|up\|down\|reset\|setup\|card`。 |
| [`labcard.py`](labcard.py) | 设备卡：从在线连接器与设备清单读取的确切工具/能力名、参数、单位与限值（包括已安装的市场设备）；作为项目的智能体上下文。 |
| [`activity.py`](activity.py) | 实验室面板的数据：端点状态、来自持久化证据链的全部回执、图表数据、离线证据链校验、操作员急停与复位。 |
| [`lims.py`](lims.py) | 带文件锁的样品登记簿，由反应器（贴标签）与工作站（扫码）共享。 |
| [`instruments/`](instruments/) | 模拟仪器，每台一个 `mhs.driver` 包。 |
