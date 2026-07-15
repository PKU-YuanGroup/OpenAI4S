<div align="center">

<img src="readme-gifs-hd/openai4s_penta.gif" alt="OpenAI4S · 面向科学家的开源 AI" width="480"/>

### 面向科学家的开源 AI

## 💸 9.9 元豆包 API 复刻 Claude Science

**一个开源的混合式科研智能体。**<br/>
<sub>原生 JSON 工具负责编排与权限；持久 Python/R 内核负责科学执行。</sub>

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-d97706.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3fb950.svg">
  <img alt="Core deps" src="https://img.shields.io/badge/core-pure%20stdlib-58a6ff.svg">
  <img alt="Paradigm" src="https://img.shields.io/badge/paradigm-Code--as--Action-bc8cff.svg">
  <img alt="Tests" src="https://img.shields.io/badge/tests-offline%20suite-3fb950.svg">
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

## 🧬 JSON 编排，Code-as-Action 科学执行

OpenAI4S 刻意保留两个动作平面。供应商原生 **JSON tool call** 处理确定性编排、权限、元数据、外部服务和人工审批；**Python/R Code-as-Action** 在持久内核中执行计算、探索、分析、仿真与长时任务。Python Cell 运行期间可以同步调用内核中的 `host` API；R 是独立的持久分析通道。

工具和代码并非二选一，它们各自承担适合的职责。纯工具或对话型任务可以通过 Engine 自有、严格结构化的 `finalize_response` 完成。科学 Python Cell 保留 `host.submit_output(...)` 契约，包括结构化 Artifact 与指标。`host.submit_output` 是唯一能从 Cell **内部**发出的完成信号；先执行过 Cell 后，后续单独且有效的 `finalize_response` 仍可关闭 Engine。

<table>
<tr><th></th><th>JSON 控制平面</th><th>Python/R 科学平面</th></tr>
<tr><td align="right"><b>适用场景</b></td><td>工作流、权限、元数据、服务</td><td>计算、分析、仿真</td></tr>
<tr><td align="right"><b>动作单元</b></td><td>一个有序原生工具批次</td><td><b>一个完整代码 Cell</b></td></tr>
<tr><td align="right"><b>组合方式</b></td><td>可审计 schema 与资源策略</td><td><code>for</code>、<code>if</code>、库；Python 还支持 Cell 中途 Host RPC</td></tr>
<tr><td align="right"><b>状态</b></td><td>追加式 Action Ledger</td><td>内核内存 + 版本化 Artifact</td></tr>
<tr><td align="right"><b>完成方式</b></td><td>Engine 自有 <code>finalize_response</code></td><td>Python：<code>host.submit_output(...)</code>；R：无 Cell 内完成信号</td></tr>
<tr><td align="right"><b>扩展方式</b></td><td>具名 <code>Tool</code> 子类</td><td>导入库或加载 Skill</td></tr>
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

- **🧬 混合动作引擎** —— 基于类的原生 JSON 工具负责编排，持久 Python/R 内核执行科学任务。CLI/Web 中的前台语言 slot 惰性启动；tool/finalize 路由本身不会启动它，但个别工具仍可管理专用 worker。
- **📒 Ledger-first 运行时** —— action group/event 和终止事实以追加方式记录；执行尝试、generation 生命周期、用量与 completion record 可持久和重建。
- **🐍 纯标准库核心** —— 引擎**和** Web 服务器都是纯标准库(`http.server` + 手写 WebSocket，无框架、无依赖)。LLM 客户端仅用 `urllib` 直接对接 OpenAI / Anthropic / Gemini。
- **🔌 一行切换多供应商** —— `ark`(doubao · glm · kimi · deepseek · minimax)加官方 `chatgpt · claude · gemini`,都由一个 `host.llm` 统一封装;在 UI 里即可切换。
- **🖥️ 科研工作台** —— 实时流式事件、版本化 Artifact、溯源、Action Timeline，以及**默认只读的 Notebook**。只有显式开启开发标志后，才能对共享 Python/R 内核输入多行代码。
- **🔐 分层本地执行防护** —— 严格子进程环境 allowlist、持久审批、与 generation 绑定的一次性 `host.bash` capability，以及 macOS Seatbelt/Linux bubblewrap 沙箱适配器；降级与 fail-closed 状态会显式呈现。
- **🔬 32 个内置 Skill** —— GPU/模型科学 Skill(AlphaFold2 · ESMFold2 · Boltz · Chai-1 · OpenFold3 · ProteinMPNN · ESM-2 · Evo2 · Borzoi · scGPT · scVI · DiffDock ……)+ 科研工作流 Skill。Skill 是**代码配方**,不是 JSON schema。
- **☁️ BYOC 远程计算** —— 在 provider 已配置且可达时，可通过 `ssh:<alias>` 或内置 **NVIDIA NIM** 集成投送 GPU 作业。通用远程计算仍属 Prototype；`host.fold` 遵守严格的不伪造策略。

---

## 🎬 效果演示

<table>
<tr>
  <td width="50%"><b>Live API 工作流</b>:从 UniProt / RCSB 到 3D 结构和报告<br/><img src="readme-gifs-hd/demo-01-hd.gif" alt="Live API 工作流:从 UniProt / RCSB 到 3D 结构和报告"></td>
  <td width="50%"><b>真实数据分析</b>:人胰岛素 INS 从 UniProt / RCSB 到可复现报告<br/><img src="readme-gifs-hd/demo-05-hd.gif" alt="真实数据分析:人胰岛素 INS 从 UniProt / RCSB 到可复现报告"></td>
</tr>
<tr>
  <td width="50%"><b>可视化 Artifact 编辑</b>:一句话把 confidence 阈值线抬到 75<br/><img src="readme-gifs-hd/demo-02-hd.gif" alt="可视化 Artifact 编辑:一句话把 confidence 阈值线抬到 75"></td>
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

`setup.sh` 用 **uv** 创建轻量控制面 `.venv`。如需完整的 Python + R 科学计算内核，请先安装 `micromamba`、`mamba` 或 `conda`，然后改用 `./setup.sh --with-kernel-envs`；已有环境可用 `./setup.sh --update-kernel-envs` 同步，且不会删除用户自行安装的包。`start.sh` 从环境中启动守护进程 + Web UI。启动无需 API Key —— **在 UI 里设置你的模型**(Customize → Models)。不启动 UI 跑单个任务:`uv run openai4s run "Compute the mean of [4,8,15,16,23,42] and submit it." -v`。

### macOS 应用（无需任何工具链）

Apple Silicon 用户可以完全跳过 clone：从 [最新 Release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/latest) 下载 `OpenAI4S-<version>-macos-arm64.dmg`，拖进「应用程序」即可启动。镜像内嵌了自带的 Python 以及默认内核科学栈——numpy · pandas · scipy · matplotlib · scikit-learn · **rdkit**（化学信息学）· **scanpy** 及单细胞栈 · umap · numba · biopython——首次启动不联网、不 `pip`。数据仍写在 `~/.openai4s`。

该构建仅做 ad-hoc 签名、**未做公证（notarization）**，所以首次打开会被 Gatekeeper 拦下。**macOS 15+**：先双击一次，关掉提示，再到「系统设置 → 隐私与安全性」点 **仍要打开**；**macOS 12–14**：右键点应用 → **打开** → **打开**。两个版本都可以直接用 `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app` 解除。

命令行随应用一起打包，想挂到 PATH 上就建个软链：

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup        # 仅当你需要 R 内核：需要先装 micromamba/mamba/conda
```

R 内核未被打包（它需要一个 conda 环境）。Intel Mac 与 Linux 请改用 PyPI 安装（`pip install openai4s`）。

---

## 📚 文档

中英双语的标准公开文档发布在 **[openai4s.org/docs](https://openai4s.org/docs/)**。文档源码与 issue 追踪位于 [Nobody-Zhang/openai4s-docs](https://github.com/Nobody-Zhang/openai4s-docs)；下表链接指向与源码仓库同步保留的代码就近文档。

| 文档 | 内容 |
|---|---|
| [**架构**](docs/architecture.md) | 混合动作路由、Action Ledger、`host` RPC 与惰性内核 |
| [**后端扩展指南**](docs/backend-extension-guide.md) | 新 Tool、Host service、repository 与 session 行为应归属的位置 |
| [**Skills**](docs/skills.md) | 32 个内置 Skill + 如何自撰 |
| [**远程计算**](docs/compute.md) | BYOC GPU 作业、`host.fold`、自动预置 |
| [**Web 应用**](docs/webapp.md) | UI 功能、Action Timeline、只读 Notebook、Artifact 与实现状态 |
| [**Jupyter 适配器**](docs/jupyter.md) | 可选的独立 Python/R KernelSpec、安装命令与兼容边界 |
| [**配置**](docs/configuration.md) | 模型供应商、环境变量、conda 环境、CLI |
| [**安全**](docs/security.md) | 纵深防御安全层与远程访问说明 |

---

## 🗺️ 路线图

- [x] 交付下一代工作台地基：分支激活与追加式 Revert/Undo 投影、带明确 Partial/Failed
  状态的验证式恢复、依赖级过期传播、持久化委派、隔离的可移植 Session 包、检查点化的
  plan/review/memory 状态，以及专用的 2D 化学/基因组/序列/MSA/LaTeX 渲染器。内存中的
  任意命名空间对象有意不做序列化；除非有安全配方能重建并验证它们，否则恢复始终是
  Partial，而且只有带可证明检查点映射的记录才提供 Fork，更早的历史会返回 409。
- [ ] 在可用平台上加强 bubblewrap 之外的 Linux 隔离（例如 seccomp），并扩展打包后沙箱冒烟验证。
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
./setup.sh                          # uv sync --locked --extra science + pre-commit hook
./setup.sh --with-kernel-envs       # 可选：完整 Python + R 内核环境
uv run pytest                       # 离线测试套件(LLM 被 mock)
uv run pre-commit run --all-files   # 全量格式化 + lint
```

代码风格由 **pre-commit** 强制执行 —— `black`、`isort`(`--profile black`)、`ruff`(版本锁定在 [`.pre-commit-config.yaml`](.pre-commit-config.yaml))。运行时依赖:核心**零依赖**(纯标准库);可选 `science` extra 锁定 `numpy>=1.24 · pandas>=2.0 · matplotlib>=3.7`。

### 欢迎的贡献

- **新 Skill** —— 在 `skills/` 下放一个 `SKILL.md`(+ 可选 `kernel.py`)—— 代码配方,而非 schema。
- **新供应商** —— 在 [`openai4s/llm/providers/`](openai4s/llm/providers/) 添加 wire adapter 并更新 provider definition/registry，或添加 BYOC 计算提供方。
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

<a href="https://www.star-history.com/?repos=PKU-YuanGroup%2FOpenAI4S&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&theme=dark&legend=top-left&sealed_token=tXwdGRCr3f-z1z8jgUOb1LUaPHwq9008wrTkaLBHVH4lQQDeSr_uyDT_1NcLONdaOxKx9l0uvSHEToe73WVGac02UiFVnXE-W_0z8C1AFwJfPJ0S87FJYQ" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&legend=top-left&sealed_token=tXwdGRCr3f-z1z8jgUOb1LUaPHwq9008wrTkaLBHVH4lQQDeSr_uyDT_1NcLONdaOxKx9l0uvSHEToe73WVGac02UiFVnXE-W_0z8C1AFwJfPJ0S87FJYQ" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&legend=top-left&sealed_token=tXwdGRCr3f-z1z8jgUOb1LUaPHwq9008wrTkaLBHVH4lQQDeSr_uyDT_1NcLONdaOxKx9l0uvSHEToe73WVGac02UiFVnXE-W_0z8C1AFwJfPJ0S87FJYQ" />
 </picture>
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

<!-- CONTRIBUTORS:START -->
<a href="https://github.com/Nobody-Zhang" title="Nobody-Zhang"><img src=".github/contributors/Nobody-Zhang.png" width="64" height="64" alt="Nobody-Zhang" /></a>
<a href="https://github.com/Grace-xyx" title="Grace-xyx"><img src=".github/contributors/Grace-xyx.png" width="64" height="64" alt="Grace-xyx" /></a>
<a href="https://github.com/wangyu-sd" title="wangyu-sd"><img src=".github/contributors/wangyu-sd.png" width="64" height="64" alt="wangyu-sd" /></a>
<a href="https://github.com/yusowa0716" title="yusowa0716"><img src=".github/contributors/yusowa0716.png" width="64" height="64" alt="yusowa0716" /></a>
<!-- CONTRIBUTORS:END -->

<sub>由 <code>scripts/update_contributors.py</code> 从 GitHub <a href="https://github.com/PKU-YuanGroup/OpenAI4S/graphs/contributors">贡献者图谱</a>自动生成(Contributors 工作流)。</sub>

---

<div align="center">
<sub><b>OpenAI4S</b> · 代码即行动,内核即环境。 · <a href="README.md">English</a></sub>
</div>
