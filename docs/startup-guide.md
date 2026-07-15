<a id="en"></a>

# Startup guide — macOS app (`.dmg`)

**English** · [简体中文](#zh)

This walks a brand-new macOS user from the downloaded disk image to a first
answer: install the app, get past Gatekeeper, point it at a model, and (so the
agent can read the live literature and databases) point it at web search. No
command line and no Python toolchain are required for any of it.

> The `.dmg` is **Apple Silicon only**. On an Intel Mac or on Linux, install
> from PyPI instead — `pip install openai4s` — then run `openai4s serve`. The
> in-app steps below (model + search configuration) are identical once the
> workbench is open.

---

## 1. Install — drag to Applications

1. Download `OpenAI4S-<version>-macos-arm64.dmg` from the
   [latest release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/latest).
2. Double-click the `.dmg` to mount it.
3. **Drag `OpenAI4S` onto the `Applications` folder** in the same window.
4. Eject the disk image and launch **OpenAI4S** from Applications (or Spotlight).

The image already embeds its own Python plus the default kernel science stack —
numpy · pandas · scipy · matplotlib · seaborn · plotly · **rdkit**
(cheminformatics) · **scanpy** and the single-cell stack (anndata · leidenalg ·
igraph) · umap · numba · scikit-learn · statsmodels · biopython · h5py · zarr ·
pyarrow — so the first launch needs **no network and no `pip`**. All data
(SQLite database, artifacts, logs) lives under `~/.openai4s`.

## 2. First launch — get past Gatekeeper

The build is **ad-hoc signed but not notarized** (notarization needs a paid
Apple Developer identity), so Gatekeeper refuses it the *first* time only. Pick
the path for your macOS version:

| macOS version | How to open the first time |
|---|---|
| **15 Sequoia and newer** | Double-click the app, dismiss the warning, then open **System Settings → Privacy & Security** and click **Open Anyway**. |
| **12–14 (Monterey–Sonoma)** | **Right-click** (or Control-click) `OpenAI4S.app` → **Open** → **Open**. |
| **Any version, from Terminal** | `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app`, then open normally. |

Once it opens, the app starts a local daemon and opens the workbench in your
browser at **`http://127.0.0.1:8760/`**. Everything below happens in that UI.

## 3. Configure your model API

The app boots **without any API key** — a *"configure your API key"* banner
links straight to the right screen.

1. Click the **Settings** gear (top of the window) and open the **Models** tab
   (中文 UI: **设置 → 模型**).
2. Under **Add model / API**, fill in:
   - **Name** — any label you like (e.g. `Doubao`, `My Claude`).
   - **Protocol** — one of **Ark-compatible**, **OpenAI-compatible**, or
     **Anthropic-compatible** (see the table below).
   - **Base URL** — leave blank to use the protocol's default endpoint.
   - **Model id** — leave blank to use the protocol's default model.
   - **API Key** — paste your provider key.
3. Click **Add**, then click **Set active** on the new profile.

That's it — the workbench now runs on your model.

**Which protocol / key?**

| Protocol in the UI | Provider it talks to | Where to get the key |
|---|---|---|
| **Ark-compatible** | Volcengine Ark (火山方舟) — one key serves **Doubao · GLM · Kimi · DeepSeek · MiniMax** | [console.volcengine.com/ark](https://console.volcengine.com/ark) — the entry **"Small" agent plan is ¥9.9 / month (≈ US$1.4)** |
| **OpenAI-compatible** | OpenAI (`gpt-5`) or any OpenAI-compatible endpoint | your OpenAI / vendor dashboard |
| **Anthropic-compatible** | Anthropic Claude (`claude-sonnet-4-5`) | [console.anthropic.com](https://console.anthropic.com) |

> **Cheapest path:** pick **Ark-compatible** and paste a Volcengine Ark key —
> you get a Claude-Science-class agent for the price of the ¥9.9/month plan.

## 4. Configure the web-search API (Tavily)

Web search lets the agent pull live literature, database records, and data
packages instead of relying only on its training knowledge. The key lives in a
**different tab from the model key**:

1. Open **Settings → Network** (中文 UI: **设置 → 网络**).
2. Make sure **Allow network access** is toggled **on** — this master switch
   gates the agent's `web_search` / `web_fetch` / download tools.
3. Register a free account at **[tavily.com](https://tavily.com)** and copy your
   API key from its dashboard.
4. Paste it into the **Search API key (Tavily)** field
   (中文: **搜索 API Key（Tavily）**) and click **Save**.

The endpoint is fixed to `api.tavily.com`; the key is stored locally under
`~/.openai4s` and is never displayed back. **Without a Tavily key, web search
still works** via keyless scrapers (DuckDuckGo and friends), but Tavily is more
reliable and rate-limit-resistant, so it is recommended.

## 5. (Optional) Add the R kernel

The `.dmg` bundles **Python only** — the R kernel needs a Conda environment
that is too large to ship inside an image, so the R channel reports itself
unavailable rather than silently falling back to Python. To add it, expose the
bundled CLI and run the setup command:

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup        # needs a Conda-family manager: micromamba / mamba / conda
```

The same CLI also gives you `openai4s status`, `openai4s stop`, and
`openai4s url` outside the app.

## 6. You're set

Open a new chat and ask a real question — for example *"Fetch human insulin
(UniProt P01308), summarize its chains, and plot residue hydrophobicity."* The
bundled stack runs cheminformatics, single-cell, and dataframe workflows
offline; with your Tavily key configured, the agent can also reach UniProt,
RCSB PDB, NCBI, and the wider web.

**Troubleshooting**

- **Nothing opened in the browser** — visit `http://127.0.0.1:8760/` manually.
- **Logs** — `~/.openai4s/logs/app.out`.
- **Port already in use** — another instance may be running; quit it, or use the
  CLI `openai4s stop`.
- **Model calls fail** — re-check the active profile under **Settings → Models**
  and confirm the key and (if you set one) the Base URL.

---
---

<a id="zh"></a>

# 上手指南 — macOS 应用（`.dmg`）

[English](#en) · **简体中文**

本指南带一位全新的 macOS 用户从下载的磁盘镜像走到第一个结果：装好应用、通过
Gatekeeper、配好模型，再配好联网搜索（让智能体能读实时文献与数据库）。整个过程
**不需要命令行、也不需要任何 Python 工具链**。

> `.dmg` **仅支持 Apple Silicon**。Intel Mac 或 Linux 请改用 PyPI 安装——
> `pip install openai4s`，然后 `openai4s serve`。工作台打开之后，下面的应用内步骤
> （配模型 + 配搜索）完全一致。

---

## 1. 安装 —— 拖进「应用程序」

1. 从 [最新 Release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/latest)
   下载 `OpenAI4S-<version>-macos-arm64.dmg`。
2. 双击 `.dmg` 挂载。
3. 在弹出的窗口里，**把 `OpenAI4S` 拖到 `Applications`（应用程序）文件夹**。
4. 推出磁盘镜像，从「应用程序」（或聚焦搜索 Spotlight）启动 **OpenAI4S**。

镜像已内嵌自带的 Python 以及默认内核科学栈——numpy · pandas · scipy · matplotlib ·
seaborn · plotly · **rdkit**（化学信息学）· **scanpy** 及单细胞栈（anndata ·
leidenalg · igraph）· umap · numba · scikit-learn · statsmodels · biopython ·
h5py · zarr · pyarrow——所以首次启动**不联网、不 `pip`**。所有数据（SQLite 数据库、
Artifact、日志）都写在 `~/.openai4s`。

## 2. 首次启动 —— 通过 Gatekeeper

该构建**仅做 ad-hoc 签名、未做公证（notarization）**（公证需要付费的 Apple 开发者
身份），所以**只有第一次**打开会被 Gatekeeper 拦下。按你的 macOS 版本选一种方式：

| macOS 版本 | 首次打开方式 |
|---|---|
| **15 Sequoia 及更新** | 先双击应用、关掉提示，再到「**系统设置 → 隐私与安全性**」点 **仍要打开**。 |
| **12–14（Monterey–Sonoma）** | **右键**（或按住 Control 点按）`OpenAI4S.app` → **打开** → **打开**。 |
| **任意版本，用终端** | `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app`，之后正常打开。 |

打开后，应用会启动本地守护进程，并在浏览器里打开工作台
**`http://127.0.0.1:8760/`**。下面的操作都在这个界面里完成。

## 3. 配置模型 API

应用启动时**不带任何 API Key**——界面上会有一条 *「configure your API key」*
横幅，直接跳到对应页面。

1. 点右上角的 **设置**（齿轮）图标，打开 **模型** 标签页（Settings → Models）。
2. 在 **新增模型 / API** 里填：
   - **名称** —— 任意标签（如 `豆包`、`我的 Claude`）。
   - **协议** —— 选 **ark 兼容协议**、**OpenAI 兼容协议** 或 **Anthropic 兼容协议**
     （见下表）。
   - **Base URL** —— 留空则用该协议默认接入点。
   - **模型 id** —— 留空则用该协议默认模型。
   - **API Key** —— 粘贴你的供应商密钥。
3. 点 **新增**，再在新出现的配置行上点 **设为当前**。

完成——工作台现在就跑在你的模型上了。

**该选哪个协议 / 哪个 Key？**

| UI 里的协议 | 对接的供应商 | 在哪里拿 Key |
|---|---|---|
| **ark 兼容协议** | 火山方舟 Volcengine Ark —— 一个 Key 覆盖 **豆包 · GLM · Kimi · DeepSeek · MiniMax** | [console.volcengine.com/ark](https://console.volcengine.com/ark) —— 入门 **「Small」Agent 套餐仅 ¥9.9 / 月** |
| **OpenAI 兼容协议** | OpenAI（`gpt-5`）或任何 OpenAI 兼容接入点 | 你的 OpenAI / 厂商控制台 |
| **Anthropic 兼容协议** | Anthropic Claude（`claude-sonnet-4-5`） | [console.anthropic.com](https://console.anthropic.com) |

> **最省钱路线：** 选 **ark 兼容协议** 并粘贴一个火山方舟 Key——用 ¥9.9/月 套餐的价钱，
> 就能得到一个 Claude Science 级的智能体。

## 4. 配置联网搜索 API（Tavily）

联网搜索让智能体能拉取实时文献、数据库记录和数据包，而不只依赖训练知识。这个 Key
**和模型 Key 不在同一个标签页**：

1. 打开 **设置 → 网络**（Settings → Network）。
2. 确认 **允许联网** 开关处于**打开**状态——这个总开关控制智能体的
   `web_search` / `web_fetch` / 下载工具。
3. 到 **[tavily.com](https://tavily.com)** 免费注册账号，从控制台复制你的 API Key。
4. 粘贴到 **搜索 API Key（Tavily）** 输入框（占位提示「输入 Tavily API Key」），点
   **保存**。

接入点固定为 `api.tavily.com`；Key 保存在本地 `~/.openai4s`，不会回显。**不填 Tavily
Key，联网搜索仍可用**（走 DuckDuckGo 等免密钥抓取），但 Tavily 更稳定、更抗限流，
推荐配置。

## 5.（可选）加装 R 内核

`.dmg` 只打包了 **Python**——R 内核需要一个 Conda 环境，体量太大无法塞进镜像，所以
R 通道会直接报告「解释器不可用」，而不会悄悄退回 Python。要加装它，先把内置 CLI 暴露
出来，再跑 setup：

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup        # 需要一个 Conda 家族管理器：micromamba / mamba / conda
```

这个 CLI 同时提供 `openai4s status`、`openai4s stop`、`openai4s url`，可在应用之外使用。

## 6. 大功告成

开一个新对话，问个真实问题——比如 *「拉取人胰岛素（UniProt P01308），概括它的各条链，
并画出残基疏水性。」* 内置科学栈可离线跑化学信息学、单细胞和 DataFrame 工作流；配好
Tavily Key 之后，智能体还能访问 UniProt、RCSB PDB、NCBI 以及更广的互联网。

**排障**

- **浏览器没自动打开** —— 手动访问 `http://127.0.0.1:8760/`。
- **日志** —— `~/.openai4s/logs/app.out`。
- **端口被占用** —— 可能已有一个实例在跑；退出它，或用 CLI `openai4s stop`。
- **模型调用失败** —— 到 **设置 → 模型** 复查当前激活的配置，确认 Key 以及（若填过）
  Base URL 是否正确。
</content>
