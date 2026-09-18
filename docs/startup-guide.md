<a id="en"></a>

# Startup guide — macOS

**English** · [简体中文](#zh)

This walks a new macOS user to a first answer: install OpenAI4S, point it at a
model, and (so the agent can read the live literature and databases) point it
at web search.

> **v0.3.0 ships a preview macOS image, not a notarized one.** It is Apple
> Silicon only, ad-hoc signed and not notarized, so Gatekeeper blocks its first
> launch (§2). It was built and attached outside the release workflow, which
> uploads a `.dmg` only when it is Developer-ID-signed and notarized; the
> credentials for that do not exist yet. On an Intel Mac, or if you would
> rather not run an un-notarized app, install from PyPI (§1).

---

## 1. Install

### From PyPI (Intel Macs, or your own Python)

You need Python 3.10 or newer, for example from python.org or Homebrew. Install
into a virtual environment of its own, then start the daemon:

```bash
python3 -m venv ~/.venvs/openai4s && source ~/.venvs/openai4s/bin/activate
pip install "openai4s[science]"
openai4s serve
```

The `science` extra adds numpy, pandas, matplotlib and scikit-learn to the
environment the Python kernel runs in. Use `"openai4s[science,chemistry]"` if
you also want RDKit. `openai4s serve` starts the local daemon and opens the
workbench in your browser with its access token. All data (SQLite database,
artifacts, logs) lives under `~/.openai4s`. Next time, run
`source ~/.venvs/openai4s/bin/activate && openai4s serve` again.

### The v0.3.0 preview image (Apple Silicon)

1. Download `OpenAI4S-0.3.0-macos-arm64.dmg` from the
   [v0.3.0 release page](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.3.0).
   Use that pinned page rather than the newest release: a release the workflow
   produces carries no macOS image. To check the download, fetch `SHA256SUMS`
   from the same page and run `shasum -a 256 --ignore-missing -c SHA256SUMS`.
2. Double-click the `.dmg` to mount it.
3. **Drag `OpenAI4S` onto the `Applications` folder** in the same window.
4. Eject the disk image and launch **OpenAI4S** from Applications (or Spotlight).

The image embeds its own Python plus the default kernel science stack —
numpy · pandas · scipy · matplotlib · seaborn · plotly · **rdkit**
(cheminformatics) · **scanpy** and the single-cell stack (anndata · leidenalg ·
igraph) · umap · numba · scikit-learn · statsmodels · biopython · h5py · zarr ·
pyarrow — so the first launch needs **no network and no `pip`**.

If you used the v0.2.0 app before, read [Upgrading from 0.2.x](upgrading.md)
before the first launch: 0.3.0 upgrades `~/.openai4s`, and the 0.2.0 app must
not open it afterwards. The older v0.2.0 image stays on the
[v0.2.0 release page](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.2.0);
it runs 0.2.0, and its settings screens differ from the ones described below.

## 2. The preview image only — get past Gatekeeper

Skip this section if you installed from PyPI.

The image is **ad-hoc signed but not notarized** (notarization needs a paid
Apple Developer identity), so Gatekeeper refuses it the *first* time only. Pick
the path for your macOS version:

| macOS version | How to open the first time |
|---|---|
| **15 Sequoia and newer** | Double-click the app, dismiss the warning, then open **System Settings → Privacy & Security** and click **Open Anyway**. |
| **12–14 (Monterey–Sonoma)** | **Right-click** (or Control-click) `OpenAI4S.app` → **Open** → **Open**. |
| **Any version, from Terminal** | `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app`, then open normally. |

Once it opens, the app starts a local daemon and opens the workbench in your
browser. Everything below happens in that UI.

## 3. Configure your model API

OpenAI4S boots **without any API key** — a *"configure your API key"* banner
links straight to the right screen.

**Recommended Volcengine flow** (new in 0.3.0; the v0.2.0 preview image has only
the manual form further down): install the official connector once with
`npm i -g @volcengine/ark-cli@latest`, then open **Settings → Models** and click
**Continue with Volcengine**. OpenAI4S opens the official Volcengine
authorization page in your browser. Finish authorization, copy the complete
authorization string shown there (usually Base64 text containing `code` and
`state`), and paste it back into OpenAI4S; do not copy only the inner `code`.
No system terminal is required. OpenAI4S
discovers your Agent Plan or Coding Plan, shows its current quota, and activates
it automatically when there is only one ready choice. Multiple plans get a
selector. A connected account with no plan, API Key, team seat, or remaining
quota stays visibly connected and shows the exact next action. After creating a
Key in the console, OpenAI4S checks for it automatically for two minutes; the
manual **Recheck** action remains available. One usable Key continues setup
automatically, while multiple Keys get a masked name/suffix selector. OpenAI4S
never receives the account password, and the Ark API Key is
handed directly to the local SecretBroker instead of being returned to the
browser. If the Ark CLI later requires a one-time Project setup, the page says
so explicitly and provides the exceptional CLI command to run. OpenAI4S reuses
the Project selected by Ark CLI and never assumes that a `default` Project exists.

For another provider, or to create a profile manually:

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

## 4. Configure web search (Doubao Search Custom)

Web search lets the agent pull live literature, database records, and data
packages instead of relying only on its training knowledge. **Doubao Search
Custom is the primary option** and uses an Ark Agent Plan Key:

1. Open **Settings → Network** (中文 UI: **设置 → 网络**).
2. Make sure **Allow network access** is toggled **on** — this master switch
   gates the agent's `web_search` / `web_fetch` / download tools.
3. Paste your Ark **Agent Plan Key** into the **Doubao Search Custom** card and
   click **Save credential**. If the active Ark model profile already uses that
   same key, OpenAI4S reuses it automatically; the password field is cleared
   immediately after saving and the key is never displayed back.
4. Run one query in the card. The UI marks Doubao available only after the
   direct provider returns at least one real result; this check never falls
   back to another search engine.

The Agent Plan Key is stored through the selected local SecretBroker backend;
business settings under `~/.openai4s` keep only an opaque reference. The
explicit `OPENAI4S_SECRET_STORE=plaintext` backend is the documented exception
that stores the broker value in SQLite. The credential is shared with
the managed DataPro professional-dataset connector. **Tavily remains available
as a backup** in the same Network screen, and keyless scrapers (DuckDuckGo and
friends) remain a final backup for the separate generic search path. Their
results never make the dedicated Doubao check claim success.

## 5. (Optional) Add the R kernel

Neither install route ships R. Without an R interpreter the R channel reports
itself unavailable rather than silently falling back to Python. The documented
way to add one is a Conda environment built by `openai4s setup`, which needs a
Conda-family manager (`micromamba`, `mamba` or `conda`).

With a PyPI install, run the setup command from the same virtual environment:

```bash
openai4s setup
```

With the preview image, expose its bundled CLI first:

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup
```

Either way the same CLI also gives you `openai4s status`, `openai4s stop`, and
`openai4s url`.

## 6. You're set

Open a new chat and ask a real question — for example *"Fetch human insulin
(UniProt P01308), summarize its chains, and plot residue hydrophobicity."* The
kernel runs dataframe and plotting workflows with the `science` extra, and
cheminformatics with `chemistry` (the preview image bundles both plus the
single-cell stack). With Doubao Search authorized by your Agent Plan Key, the
agent can also reach current sources across the wider web. Tavily and keyless
engines remain available as backups.

**Troubleshooting**

- **Nothing opened in the browser** — run `openai4s url` and open the URL it
  prints. The bare `http://127.0.0.1:8760/` asks for the access token.
- **Logs** — a PyPI install logs to the terminal running `openai4s serve`; the
  preview app writes `~/.openai4s/logs/app.out`.
- **Port already in use** — another instance may be running; quit it, or use the
  CLI `openai4s stop`.
- **Model calls fail** — re-check the active profile under **Settings → Models**
  and confirm the key and (if you set one) the Base URL.

---
---

<a id="zh"></a>

# 上手指南 — macOS

[English](#en) · **简体中文**

本指南带一位全新的 macOS 用户走到第一个结果：装好 OpenAI4S、配好模型，再配好联网搜索（让智能体能读实时文献与数据库）。

> **v0.3.0 附带的是 macOS 预览镜像，不是经过公证的镜像。** 它仅支持 Apple Silicon，只做了 ad-hoc 签名、未经公证，所以第一次打开会被 Gatekeeper 拦下（第 2 节）。它是在发布流程之外构建并挂上去的：发布流程只在 `.dmg` 经过 Developer ID 签名并完成公证时才上传它，而这些凭据目前还不存在。Intel Mac，或不想运行未公证的应用时，请从 PyPI 安装（第 1 节）。

---

## 1. 安装

### 从 PyPI 安装（Intel Mac，或使用自己的 Python）

需要 Python 3.10 或更新版本，例如来自 python.org 或 Homebrew。请安装到一个单独的虚拟环境中，然后启动守护进程：

```bash
python3 -m venv ~/.venvs/openai4s && source ~/.venvs/openai4s/bin/activate
pip install "openai4s[science]"
openai4s serve
```

`science` extra 会把 numpy、pandas、matplotlib 和 scikit-learn 装进 Python 内核所用的环境；如果还需要 RDKit，请用 `"openai4s[science,chemistry]"`。`openai4s serve` 会启动本地守护进程，并用访问令牌在浏览器里打开工作台。所有数据（SQLite 数据库、Artifact、日志）都写在 `~/.openai4s`。下次使用时，再运行一次 `source ~/.venvs/openai4s/bin/activate && openai4s serve`。

### v0.3.0 预览镜像（Apple Silicon）

1. 从 [v0.3.0 release 页面](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.3.0)下载 `OpenAI4S-0.3.0-macos-arm64.dmg`。请使用这个固定页面，不要去最新 Release 里找：由发布流程产出的 release 不带 macOS 镜像。想校验下载，可从同一页面取 `SHA256SUMS`，然后运行 `shasum -a 256 --ignore-missing -c SHA256SUMS`。
2. 双击 `.dmg` 挂载。
3. 在弹出的窗口里，**把 `OpenAI4S` 拖到 `Applications`（应用程序）文件夹**。
4. 推出磁盘镜像，从「应用程序」（或聚焦搜索 Spotlight）启动 **OpenAI4S**。

镜像已内嵌自带的 Python 以及默认内核科学栈——numpy · pandas · scipy · matplotlib · seaborn · plotly · **rdkit**（化学信息学）· **scanpy** 及单细胞栈（anndata · leidenalg · igraph）· umap · numba · scikit-learn · statsmodels · biopython · h5py · zarr · pyarrow——所以首次启动**不联网、不 `pip`**。

如果之前用过 v0.2.0 应用，首次启动前请先阅读[从 0.2.x 升级](upgrading_zh.md)：0.3.0 会升级 `~/.openai4s`，之后 0.2.0 应用不能再打开它。旧的 v0.2.0 镜像仍在 [v0.2.0 release 页面](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.2.0)；它运行的是 0.2.0，设置页面也与下文描述的不同。

## 2. 仅适用于预览镜像 —— 通过 Gatekeeper

如果你是从 PyPI 安装的，请跳过本节。

该镜像**仅做 ad-hoc 签名、未做公证（notarization）**（公证需要付费的 Apple 开发者身份），所以**只有第一次**打开会被 Gatekeeper 拦下。按你的 macOS 版本选一种方式：

| macOS 版本 | 首次打开方式 |
|---|---|
| **15 Sequoia 及更新** | 先双击应用、关掉提示，再到「**系统设置 → 隐私与安全性**」点 **仍要打开**。 |
| **12–14（Monterey–Sonoma）** | **右键**（或按住 Control 点按）`OpenAI4S.app` → **打开** → **打开**。 |
| **任意版本，用终端** | `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app`，之后正常打开。 |

打开后，应用会启动本地守护进程，并在浏览器里打开工作台。下面的操作都在这个界面里完成。

## 3. 配置模型 API

OpenAI4S 启动时**不带任何 API Key**——界面上会有一条 *「configure your API key」*
横幅，直接跳到对应页面。

**推荐的火山流程**（0.3.0 新增；v0.2.0 预览镜像只有下文的手动表单）：先用 `npm i -g @volcengine/ark-cli@latest` 一次性安装官方
连接器，然后打开 **设置 → 模型**，点击 **使用火山引擎登录**。OpenAI4S 会在浏览器中打开
火山官方授权页。完成授权后，复制页面显示的完整授权字符串（通常是一段包含 `code` 和 `state`
的 Base64 文本），再粘贴回 OpenAI4S；不要只复制其中的 `code`。不需要打开系统终端。
OpenAI4S 会发现 Agent Plan 或 Coding Plan、显示当前额度，并在只有一个已就绪套餐时直接启用；
多个套餐则先让用户选择。账号即使缺少套餐、API Key、团队席位或可用额度，也会保持“已连接”，
并显示准确的下一步。在控制台创建 Key 后，OpenAI4S 会自动检查两分钟，同时保留 **重新检查**；
只有一把可用 Key 时直接继续，多把 Key 时显示名称与末四位供选择，全程不需要复制粘贴。
OpenAI4S 不接收账号密码，Ark API Key 也不会返回浏览器，而是直接交给本机 SecretBroker。
如果 Ark CLI 后续要求一次性的 Project 设置，页面会明确提示，并提供需要执行的例外 CLI 命令。
OpenAI4S 只复用 Ark CLI 已选定的 Project，不会假设账号一定存在名为 `default` 的 Project。

其他供应商，或者需要手动新建配置时：

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

## 4. 配置联网搜索（豆包搜索 Custom 版）

联网搜索让智能体能拉取实时文献、数据库记录和数据包，而不只依赖训练知识。**豆包搜索
Custom 版是主选项**，使用火山方舟 Agent Plan Key：

1. 打开 **设置 → 网络**（Settings → Network）。
2. 确认 **允许联网** 开关处于**打开**状态——这个总开关控制智能体的
   `web_search` / `web_fetch` / 下载工具。
3. 把火山方舟 **Agent Plan Key** 粘贴到**豆包搜索 Custom 版**卡片，点**保存凭证**。
   当前 Ark 模型配置已经使用同一个 Key 时，OpenAI4S 会自动复用；保存后密码输入框立即
   清空，Key 也不会回显。
4. 在卡片里真实查询一次。只有豆包直连返回至少一条真实结果时，UI 才标记豆包可用；
   这个专用检查绝不回退到其他搜索引擎。

Agent Plan Key 由所选的本地 SecretBroker 后端保存；`~/.openai4s` 下的业务设置只保留
不透明引用。显式启用 `OPENAI4S_SECRET_STORE=plaintext` 是例外，会把 broker 值保存到
SQLite。该凭证与托管的 DataPro 专业数据集连接器共用。**Tavily 仍在同一「网络」页面作为备用**，
DuckDuckGo 等免密钥抓取则是独立通用搜索路径的最后备用；它们的结果都不会让豆包专用
检查冒充成功。

## 5.（可选）加装 R 内核

两种安装方式都不带 R。没有 R 解释器时，R 通道会直接报告「解释器不可用」，而不会悄悄退回 Python。文档给出的加装方式是用 `openai4s setup` 构建一个 Conda 环境，这需要一个 Conda 家族管理器（`micromamba`、`mamba` 或 `conda`）。

如果是 PyPI 安装，在同一个虚拟环境里运行 setup：

```bash
openai4s setup
```

如果用的是预览镜像，先把它内置的 CLI 暴露出来：

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup
```

无论哪种方式，这个 CLI 都同时提供 `openai4s status`、`openai4s stop`、`openai4s url`。

## 6. 大功告成

开一个新对话，问个真实问题——比如 *「拉取人胰岛素（UniProt P01308），概括它的各条链，并画出残基疏水性。」* 装了 `science` extra 的内核可以跑 DataFrame 与绘图工作流，装了 `chemistry` 可以跑化学信息学（预览镜像两者都内置，另有单细胞栈）。配好 Agent Plan Key 授权豆包搜索之后，智能体还能检索更广的实时互联网；Tavily 与免密钥引擎继续作为备用。

**排障**

- **浏览器没自动打开** —— 运行 `openai4s url`，打开它打印出的 URL。直接访问 `http://127.0.0.1:8760/` 会要求访问令牌。
- **日志** —— PyPI 安装的日志输出在运行 `openai4s serve` 的终端里；预览应用写入 `~/.openai4s/logs/app.out`。
- **端口被占用** —— 可能已有一个实例在跑；退出它，或用 CLI `openai4s stop`。
- **模型调用失败** —— 到 **设置 → 模型** 复查当前激活的配置，确认 Key 以及（若填过）
  Base URL 是否正确。
</content>
