# Web UI（浏览器界面）

[English](README.md)

标准库 Gateway 在 `/static/` 下提供这棵树。默认工作台外壳是 `dist/` 里提交的 Vite 构建产物（源码在 [`../../../frontend/`](../../../frontend/)）。`OPENAI4S_WEBUI=legacy` 改发本目录冻结的 `index.html` + `app.js`。`frontend/` 里的改动需要 `npm run build`（或对着正在跑的 daemon 用 `npm run dev`），刷新页面不会自动生效。安装后的 wheel 提供包内副本。两套外壳都在应用 CSP 下加载外链脚本。客户端通过 REST 读取和写入，并跟随一条 WebSocket 事件流；规范会话状态始终在服务端。

分子查看器只加载自带的 3Dmol；运行时缺失时回退到文本，不向 CDN 请求。Ketcher 3.7.0 位于 `vendor/ketcher/`，在独立但与应用同源的 `/ketcher` 文档中运行，保留鉴权及第一方 API 桥接。仅精确的编辑器文档 `/static/vendor/ketcher/index.html` 放行上游运行时所需的 `unsafe-eval`；包装页、应用外壳及 Artifact 策略均不增加该例外。Stage 9 关闭或资产缺失时，路由返回占位页。Artifact HTML 使用另一条信任边界：可执行预览位于具有范围限制的另一个 loopback origin，详见下文。

## 运行时职责

- 整个产品由两层壳撑起来：Dashboard 和对话 Workspace。两者一起提供项目、会话、对话、模型选择、计划/审阅、审批、Artifact、Notebook（含按语言拆分的 `.ipynb`、打包 bundle 与 Markdown 三类导出）、Timeline、Context、Security、分支/恢复、Skill、连接器与设置界面。
- Timeline 这个界面现在是一个可交互的行动账本。上方是可缩放的概览条，画出每次 attempt 的真实阶段（排队、首响应、解码）：悬停给出精确时刻，拖选变成时间过滤，滚轮缩放，键盘可以逐行浏览；下方的账本行按窗口虚拟化、按 Turn 分组、可搜索——搜索只覆盖已加载的窗口，而且会明说。所有这些读的都是有界的 Action Ledger 投影；原始参数和 provider wire state 从不进入这里。
- Auto Mode 按 stage 门控的界面同样是投影。以候选身份流式输出的回答带一枚审阅徽章，徽章跟随 completion gate 的裁定而不是跟随文字本身；一条常驻的就绪横幅指出 standard 档缺了什么，并提供只可复制的托管修复命令；Stage 9 的 Artifact workbench 增加了数据集表格的分页与筛选、版本 diff、按位置评论，以及「在 Ketcher 中编辑」。compute 徽章和运行位置对话框会说明会话内核在哪里运行、还在等哪一个条件，另有横幅标出内核活动状态丢失所在的 epoch。连接器配置通过显式 env patch 编辑：只列出已配置密钥的*名字*，密钥值绝不往返浏览器。
- 现行工作台在 `frontend/`。`app.js` 是冻结的逃生舱，在 `OPENAI4S_WEBUI=legacy` 时仍保存同一套投影。核心 Workbench 投影都经过显式的净化处理，既不保留 provider 的原始报文，也不保留工具调用的原始参数。不要往 `app.js` 加新功能。
- WebSocket 事件驱动流式文本、Cell、activity、执行所有权以及 Workbench 的读模型；REST 负责有界读取和显式写入。订阅时会带上 `since_seq` 和这条流的 `epoch`，所以重连是在 `replay_begin`/`replay_end` 之间续传，而不是整段重取；而 epoch 一变，这个标签页手里的游标全部作废，绝不会拿去给一条本 daemon 从未产生过的流编号。一轮由它的 `execution_id` 追踪，绝不用它所在的会话来追踪：frame 活得比 turn 长，两个 turn 也会重叠。
- 一次应答丢失会毁掉的那点状态，由客户端自己保管。钉住评论的 admission id 在这里生成，取自平台 CSPRNG，并在消息**发出之前**写进 `localStorage`，于是一个没收到 202 的标签页可以去问这些评论后来怎么了，而不必重发、也不必悄悄丢掉。长列表是分页而不是截断：会话列表跟着服务端那个不透明的 keyset 游标走，配一个「加载更多」控件；更早的一页消息按时间插入而不是追加，因为这一栏里本来就有比最新一页消息更早的 activity 步骤。
- Artifact 的渲染器由服务端 [`../renderers.py`](../renderers.py) 决定。sequence、alignment、genome、Molfile/SMILES 与 LaTeX 的解析在 [`scientific_renderers.js`](scientific_renderers.js) 中；现行查看器在 `frontend/` 中组合。HTML preview 最初使用空 iframe sandbox；只有 grant 请求成功且 origin 校验通过，才在另一个 loopback 主机名上升级为 `allow-scripts allow-same-origin`，支持交互报告与授权范围内的同目录资源。服务端把 grant 绑定到该 Host、签发时的父页面 origin、非空 frame 及过期时间；应用 Host 无法使用它。不支持的部署与失败的 grant 保持静态预览。应用源上的 Artifact 响应仍带禁止脚本的 CSP，PDF iframe 仍使用空 sandbox。grant URL 是脚本可读取的 bearer 凭证；CSP 不能阻止 iframe 通过自身导航向外携带数据。见[安全契约与剩余风险](../../../docs/security.md#executable-artifact-previews-use-a-scoped-alternate-origin)。
- 新的工作台 UI 在 [`../../../frontend/`](../../../frontend/)。请保持 DOM ID 和事件名稳定，离线静态契约测试和浏览器冒烟测试都是照着它们写的。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`app.js`](app.js) | 冻结的逃生舱（`OPENAI4S_WEBUI=legacy`）。不要在这里加功能；新的工作台 UI 在 [`../../../frontend/`](../../../frontend/)。 |
| [`favicon.js`](favicon.js) | 浏览器支持时用 WebCodecs 逐帧播放 GIF favicon，标签页隐藏时暂停，钳制到 10 fps，不支持时回退到静态 GIF。 |
| [`ketcher-page.js`](ketcher-page.js) | `/ketcher` 宿主页面的脚本：把选中的 Artifact 载入自带编辑器，并把新的结构版本 POST 回去。外链的理由和 `login.js` 相同——共享 CSP 只放行同源脚本，它替换掉的那段内联 `<script>` 会被直接拒绝，编辑器根本初始化不了。Artifact id 通过 `data-artifact-id` 属性传入，而不是插进可执行源码里。 |
| [`login.html`](login.html) | 团队模式登录页（`OPENAI4S_TEAM_MODE`）。只用内联样式，可执行代码在共享 CSP 下保持外链。两种模式下都在 `/login` 提供；守卫把未登录的浏览器 303 到这里。 |
| [`login.js`](login.js) | 登录页脚本：POST `/api/v1/auth/login`，把失败原因一句话展示出来；已登录或团队模式关闭时直接跳回首页。 |
| [`replay.html`](replay.html) | 只读回放查看页（M2-3）——guest 的全部界面，也是成员的快速一瞥。在登录守卫之后的 `/replay` 提供；只用内联样式，脚本外链以过 CSP。 |
| [`replay.js`](replay.js) | 拉取 `GET /api/v1/sessions/{id}/replay`（现场构建的脱敏 web-share view.json），把消息与科学 cell 渲染成朴素的转录稿。 |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | 打包的 favicon 源文件：动画解码的帧来自它，静态回退图标也是它。 |
| [`index.html`](index.html) | Dashboard、对话 Workspace、composer、右侧 dock、dialog 与设置的可访问 DOM 骨架。它在首屏绘制前应用主题，并引用静态脚本与样式。 |
| [`scientific_renderers.js`](scientific_renderers.js) | 零依赖的 sequence/MSA、genome、Molfile/SMILES、LaTeX 解析与辅助函数，外加渲染器描述符校验。它们只产出普通数据、绝不产出 HTML，DOM 由查看器依据这些记录构建；一层薄薄的 UMD 包装让 Node 契约测试能直接导入同一个文件。现行 table/image/PDF/HTML/text 展示在 `frontend/` 中；旧版实现保留在 `app.js` 中。 |
| [`style.css`](style.css) | 整套视觉系统：明暗 token（含 `--text-100/--text-300/--surface-0/--warn`）、字体、Dashboard/Workspace 布局、Activity 与 Artifact 组件、dialog、可访问性以及移动端断点。`scripts/check_css_tokens.py` 要求每一处 `var(--x)` 都有声明。 |
| [`theme-bootstrap.js`](theme-bootstrap.js) | 在解析文档 head 时、body 绘制前应用已保存的明暗主题；外链后 CSP 授权不再依赖内联 HTML 的 hash。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `dist/` | [`../../../frontend/`](../../../frontend/) 提交进来的 Vite 构建产物。默认 SPA 外壳在 `/` 与工作台深链；`/static/dist/` 无论开关都是本目录下的普通静态树。在 `frontend/` 里 `npm run build` 重建，并把产物和源码同一 PR 提交；不要手改带哈希的资源。脚本全部是带 `src=` 的外链，这棵树仍落在 CSP `script-src 'self'` 里。`OPENAI4S_WEBUI=legacy` 是改发 `index.html` 的逃生舱。 |
| `share/` | 独立的只读分享查看器（`share.html`/`share.js`/`share.css`），由 relay 隧道的 ShareRouter 提供，与主单页应用分开，而且是自成一体的，不是主应用的裁剪版。它的外壳只加载 `share.js` 和 `share.css`，别的一概不加载：没有 WebSocket，不与 `app.js` 共享任何状态，Markdown 和 CSV 也由它自带的极简渲染器处理，而不是 `scientific_renderers.js`。ShareRouter 的资产白名单确实放行了 `scientific_renderers.js` 和自带的 3Dmol，好让更完整的查看器可以取用，但当前这套外壳两者都不请求。 |
| `vendor/` | 从上游取来的第三方资产：压缩版 3Dmol 运行时、`ketcher/` 下钉住的 Ketcher 3.7.0 独立版，以及字体文件。3Dmol 是 `app.js` 唯一会注入自己页面的第三方 JavaScript，而且只有在打开分子 Artifact 时才注入；自带的那份如果加载不上，Artifact 直接退回纯文本展示，不存在 CDN 回退（见 [`app.js`](app.js) 里注入 `3Dmol-min.js` 的那处 script 标签）。Ketcher 从不加入主文档：Gateway 把它作为独立的 `/ketcher` 页面提供，Stage 9 打开时由 workbench 用 iframe 加载。把它们当作上游的、逐字节敏感的资产：不参与格式化，本 README 也不逐个文件说明。 |

## 验证

在仓库根目录运行离线 UI 契约：

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

只要改动涉及交互或流式传输，就必须过一遍真实浏览器：启动 `./start.sh`，把真正的 WebSocket 流程跑一遍。另见 [Server 包总览](../README_zh.md)与 [Web 应用指南](../../../docs/webapp.md)。
