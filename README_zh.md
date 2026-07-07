<div align="center">

<img src="readme-gifs-hd/openai4s_penta.gif" alt="OpenAI4S · 面向科学家的开源 AI" width="480"/>

### 面向科学家的开源 AI

## 💸 9.9 元豆包 API 复刻 Claude Science

**一个开源的「代码即行动」(Code-as-Action) 科研智能体。**<br/>
<sub>模型的动作空间是一个图灵完备的持久内核 —— **而不是**一份固定的工具清单。</sub>

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-d97706.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3fb950.svg">
  <img alt="Core deps" src="https://img.shields.io/badge/core-pure%20stdlib-58a6ff.svg">
  <img alt="Paradigm" src="https://img.shields.io/badge/paradigm-Code--as--Action-bc8cff.svg">
  <img alt="Tests" src="https://img.shields.io/badge/tests-206%20passing-3fb950.svg">
</p>
<p>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/PKU-YuanGroup/OpenAI4S?style=social"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/PKU-YuanGroup/OpenAI4S?style=social"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/issues"><img alt="Issues" src="https://img.shields.io/github/issues/PKU-YuanGroup/OpenAI4S"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/pulls"><img alt="PRs Welcome" src="https://img.shields.io/badge/PRs-welcome-3fb950.svg"></a>
</p>

[English](README.md) · **简体中文**

</div>

---

> [!TIP]
> **为什么是 9.9 元?** 不需要昂贵的前沿模型 Key —— OpenAI4S 跑在**豆包**上,用的是 **火山方舟** 最便宜的**「Small」套餐:¥9.9 / 月**。在 UI 里把供应商选成 `ark`,你就用不到一杯咖啡的钱得到了一个 Claude Science 级的智能体。

<div align="center">
<img src="docs/ark-agent-plan-9.9.png" alt="火山方舟 · Agent Plan —— Small 套餐 ¥9.9/月" width="760"/>
<br/>
<sub>火山方舟 · Agent Plan(个人版)—— 入门的 <b>Small</b> 套餐仅 <b>¥9.9 / 月</b>。</sub>
</div>

---

## 🧬 代码即 Agent,而非 ReAct

如今绝大多数「AI Agent」是 **ReAct + `tool_use`**:每一步,模型返回一个 `tool_use` JSON,宿主执行**单个**工具,再循环，也就是动作空间是一份固定菜单。**OpenAI4S** 则产出**一段真正的 Python/R 代码 cell**,在**持久内核**里执行;所有「工具」都是内核内 `host` 对象上的普通函数。它的动作空间是**一门图灵完备语言** —— 一个 turn 内即可循环、分支、调用库,而大对象常驻在内核内存里。

<table>
<tr><th></th><th>🧰 ReAct + <code>tool_use</code></th><th>🧬 Code-as-Action(OpenAI4S)</th></tr>
<tr><td align="right"><b>动作单元</b></td><td>一个工具调用(JSON)</td><td><b>一段任意程序</b>(代码 cell)</td></tr>
<tr><td align="right"><b>动作空间</b></td><td>预定义工具的固定菜单</td><td><b>一门图灵完备语言</b>(Python / R)</td></tr>
<tr><td align="right"><b>循环与组合</b></td><td>靠模型多轮往返编排</td><td>代码内<b>一次完成</b> —— <code>for</code>、<code>if</code>、推导式</td></tr>
<tr><td align="right"><b>中间状态</b></td><td>存在模型上下文里(文本)</td><td>存在<b>内核内存</b>里(真实对象)</td></tr>
<tr><td align="right"><b>大对象处理</b></td><td>必须序列化塞回上下文</td><td>常驻内核,<b>只回传摘要</b></td></tr>
<tr><td align="right"><b>N 个操作</b></td><td>≈ N 次模型往返</td><td><b>一次往返</b>即可完成多步</td></tr>
<tr><td align="right"><b>扩展工具</b></td><td>改 schema + 改宿主</td><td><code>import</code> 一个库,或读一个 Skill</td></tr>
<tr><td colspan="3">

```python
# ReAct 需约 14 次往返(read → … → filter → sort → plot)。   OpenAI4S:一个代码 cell。
hits   = [f for f in files if pattern in host.read_file(f)]
top3   = sorted(hits, key=os.path.getsize, reverse=True)[:3]
frames = [pd.read_csv(f) for f in top3]      # 10 万行的 DataFrame 留在内核里……
host.save_artifact(plot(frames))             # ……上下文里只留 "<DataFrame 100000×20>"
```

</td></tr>
</table>

---

## 📣 更新

- **`2026-07-06`** 🎉 **代码开源** —— 纯标准库 Code-as-Action 引擎、科研 Web 应用、24 个科学 Skill、BYOC 远程计算。

---

## 😮 亮点

- **🧬 Code-as-Action 引擎** —— 以 Jupyter 式**持久内核**作为动作空间。命名空间跨 cell 保持;大对象常驻,只有摘要进上下文。
- **🐍 纯标准库内核** —— 引擎**和** Web 服务器都是纯标准库(`http.server` + 手写 WebSocket,无框架、无依赖)。LLM 客户端仅用 `urllib` 直接对接 OpenAI / Anthropic / Gemini。
- **🔌 一行切换多供应商** —— `ark`(doubao · glm · kimi · deepseek · minimax)加官方 `chatgpt · claude · gemini`,都由一个 `host.llm` 统一封装;在 UI 里即可切换。
- **🖥️ 完整科研 Web 应用** —— 实时流式 turn、**版本化产物**(`.pdb`/`.cif` 内置 3Dmol 查看器)、共享 Agent 内核的实时 Notebook、后台运行与恢复。
- **🔬 24 个内置 Skill** —— 14 个 GPU/模型科学 Skill(AlphaFold2 · ESMFold2 · Boltz · Chai-1 · OpenFold3 · ProteinMPNN · ESM-2 · Evo2 · Borzoi · scGPT · scVI · DiffDock ……)+ 科研工作流 Skill。Skill 是**代码配方**,不是 JSON schema。
- **☁️ BYOC 远程计算** —— 通过 `ssh:<alias>` 或内置 **NVIDIA NIM** 提供方把 GPU 作业投送到你自己的机器;真实的 `host.fold`(单序列 Protenix / AF3 级),受严格的不伪造策略约束。

---

## 🎬 效果演示

<table>
<tr>
  <td width="50%"><b>Live API 工作流</b>:从 UniProt / RCSB 到 3D 结构和报告<br/><img src="readme-gifs-hd/demo-01-hd.gif" alt="Live API 工作流:从 UniProt / RCSB 到 3D 结构和报告"></td>
  <td width="50%"><b>真实数据分析</b>:人胰岛素 INS 从 UniProt / RCSB 到可复现报告<br/><img src="readme-gifs-hd/demo-05-hd.gif" alt="真实数据分析:人胰岛素 INS 从 UniProt / RCSB 到可复现报告"></td>
</tr>
<tr>
  <td width="50%"><b>可视化产物编辑</b>:一句话把 confidence 阈值线抬到 75<br/><img src="readme-gifs-hd/demo-02-hd.gif" alt="可视化产物编辑:一句话把 confidence 阈值线抬到 75"></td>
  <td width="50%"><b>注释驱动图表编辑</b>:圈选区域并重绘图例配色<br/><img src="readme-gifs-hd/demo-06-hd.gif" alt="注释驱动图表编辑:圈选区域并重绘图例配色"></td>
</tr>
<tr>
  <td width="50%"><b>计划模式科研分析</b>:青蒿素与紫杉醇溶解度预测<br/><img src="readme-gifs-hd/demo-03-hd.gif" alt="计划模式科研分析:青蒿素与紫杉醇溶解度预测"></td>
  <td width="50%"><b>蛋白质工程</b>:从序列到突变候选与结构解释<br/><img src="readme-gifs-hd/demo-04-hd.gif" alt="蛋白质工程:从序列到突变候选与结构解释"></td>
</tr>
</table>

---

## ⚡ 快速开始

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh     # 一次性:用 uv 创建环境
./start.sh     # 启动 Web UI(http://127.0.0.1:8760/)
```

`setup.sh` 用 **uv** 创建 `.venv`;`start.sh` 从中启动守护进程 + Web UI。启动无需 API Key —— **在 UI 里设置你的模型**(Customize → Models)。不启动 UI 跑单个任务:`uv run openai4s run "Compute the mean of [4,8,15,16,23,42] and submit it." -v`。

---

## 📚 文档

| 文档 | 内容 |
|---|---|
| [**架构**](docs/architecture.md) | Code-as-Action 双循环、`host` API、内核设计 |
| [**Skills**](docs/skills.md) | 24 个内置 Skill + 如何自撰 |
| [**远程计算**](docs/compute.md) | BYOC GPU 作业、`host.fold`、自动预置 |
| [**Web 应用**](docs/webapp.md) | UI 功能、实时 Notebook、产物、演示会话 |
| [**配置**](docs/configuration.md) | 模型供应商、环境变量、conda 环境、CLI |
| [**安全**](docs/security.md) | 纵深防御安全层与远程访问说明 |

---

## 🗺️ 路线图

- [ ] 本地内核的 OS 级沙箱对齐(Seatbelt / bubblewrap + seccomp)。
- [ ] DuckDuckGo 之外的免密钥 `web_search`(抗限流)。
- [ ] SSH + NVIDIA NIM 之外的更多 BYOC 提供方(Modal / SLURM)。
- [ ] 端到端科研工作流的公开基准。
- [ ] 本地 GPU 模型服务,让结构/设计类 Skill 无需远程计算即可运行。

---

## 💡 如何贡献

OpenAI4S 是一个让 **Code-as-Action** 范式保持开源的社区项目。

提 PR 前请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md) —— 它定义了分支命名、PR 检查清单([`.github/pull_request_template.md`](.github/pull_request_template.md))、代码所有权([`.github/CODEOWNERS`](.github/CODEOWNERS))、评审与发布政策,以及离线测试政策。

### 开发环境配置

需要 **Python ≥ 3.10** 与 [**uv**](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh                          # uv sync --extra science + 安装 pre-commit hook
uv run pytest                       # 离线测试套件(LLM 被 mock)
uv run pre-commit run --all-files   # 全量格式化 + lint
```

代码风格由 **pre-commit** 强制执行 —— `black`、`isort`(`--profile black`)、`ruff`(版本锁定在 [`.pre-commit-config.yaml`](.pre-commit-config.yaml))。运行时依赖:核心**零依赖**(纯标准库);可选 `science` extra 锁定 `numpy>=1.24 · pandas>=2.0 · matplotlib>=3.7`。

### 欢迎的贡献

- **新 Skill** —— 在 `skills/` 下放一个 `SKILL.md`(+ 可选 `kernel.py`)—— 代码配方,而非 schema。
- **新供应商** —— 在 [`openai4s/llm.py`](openai4s/llm.py) 里加一个协议适配器,或一个 BYOC 计算提供方。
- **引擎与 UI** —— 核心是纯标准库、可读性强;Web 应用无框架。

请保持核心零依赖,把可选科学库导入包在 `try/except ImportError` 里,并在提 PR 前确保 `uv run pytest` 与 `uv run pre-commit run --all-files` 通过。

---

## 👍 致谢与相关工作

- **Claude Science**(Anthropic)—— 作为闭源参考架构,OpenAI4S 以开源方式独立复现了它的 Code-as-Action 设计、持久内核、宿主 RPC 协议与安全层。
- **CodeAct** —— *「Executable Code Actions Elicit Better LLM Agents」* —— 以代码作为统一动作接口。
- **ReAct** —— *「Synergizing Reasoning and Acting in Language Models」* —— 本项目刻意背离的 `tool_use` 基线。
- 各科学 Skill 站在 **ColabFold / AlphaFold、ESM、OpenFold、Boltz、Chai、ProteinMPNN、DiffDock、Evo2、Borzoi、scGPT、scVI-tools** 以及开放数据服务(NCBI、UniProt、RCSB PDB、EBI、OpenAlex、Crossref)之上。

---

## 🔒 许可证

以 **MIT License** 发布 —— 见 [`LICENSE`](LICENSE)。

---

## ✨ Star 历史

<a href="https://star-history.com/#PKU-YuanGroup/OpenAI4S&Date">
  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=PKU-YuanGroup/OpenAI4S&type=Date" width="600">
</a>

---

## ✏️ 引用

```bibtex
@software{openai4s2026,
  title  = {OpenAI4S: An Open-Source Code-as-Action Scientific Research Agent},
  author = {OpenAI4S contributors},
  year   = {2026},
  url    = {https://github.com/PKU-YuanGroup/OpenAI4S},
  note   = {Open AI for Scientist —— 对 Code-as-Action 范式的纯标准库开源复现}
}
```

## 🤝 社区贡献者

<a href="https://github.com/PKU-YuanGroup/OpenAI4S/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=PKU-YuanGroup/OpenAI4S" alt="OpenAI4S contributors" />
</a>

---

<div align="center">
<sub><b>OpenAI4S</b> · 代码即行动,内核即环境。 · <a href="README.md">English</a></sub>
</div>
