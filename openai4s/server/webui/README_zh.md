# Web UI（浏览器界面）

[English](README.md)

浏览器客户端放在这里，由标准库 Gateway 在 `/static/` 下提供。这里没有 npm、没有打包器、没有编译步骤：源码 checkout 里改一行，刷新浏览器就能看到；安装后的 wheel 提供的则是包内副本。`index.html` 本身只加载 `favicon.js`、`scientific_renderers.js` 和 `app.js`，但它并不是页面上唯一的脚本来源。有一个第三方库仍会进到页面里：打开分子 Artifact 时，`app.js` 会在运行时注入 3Dmol，只取 `vendor/` 下自带的那一份，不再有别的来源。这次注入原先在本地那份加载失败时会改从 `https://3Dmol.org/build/3Dmol-min.js` 再取一次——那是一次悄无声息的真实对外请求，而且是在持有会话 Cookie 的页面里执行第三方脚本。它已经被删掉了：自带的那份加载不上时，Artifact 直接退回成纯文本展示，而这本来就是 CDN 那条路失败时的同一个结果。客户端通过 REST 读取和写入，并跟随一条 WebSocket 事件流。它手里只有会话状态的投影，规范状态始终在服务端。

## 运行时职责

- 整个产品由两层壳撑起来：Dashboard 和对话 Workspace。两者一起提供项目、会话、对话、模型选择、计划/审阅、审批、Artifact、Notebook（含按语言拆分的 `.ipynb`、打包 bundle 与 Markdown 三类导出）、Timeline、Context、Security、分支/恢复、Skill 与设置界面。
- `app.js` 保存浏览器侧的投影和临时交互状态。核心 Workbench 投影都经过显式的净化处理，既不保留 provider 的原始报文，也不保留工具调用的原始参数。部分 Settings 路径和遗留的 `innerHTML` 代码还没有做完整的转义审计。
- WebSocket 事件驱动流式文本、Cell、activity、执行所有权以及 Workbench 的读模型；REST 负责有界读取和显式写入。订阅时会带上 `since_seq` 和这条流的 `epoch`，所以重连是在 `replay_begin`/`replay_end` 之间续传，而不是整段重取；而 epoch 一变，这个标签页手里的游标全部作废，绝不会拿去给一条本 daemon 从未产生过的流编号。一轮由它的 `execution_id` 追踪，绝不用它所在的会话来追踪：frame 活得比 turn 长，两个 turn 也会重叠。
- 一次应答丢失会毁掉的那点状态，由客户端自己保管。钉住评论的 admission id 在这里生成，取自平台 CSPRNG，并在消息**发出之前**写进 `localStorage`，于是一个没收到 202 的标签页可以去问这些评论后来怎么了，而不必重发、也不必悄悄丢掉。长列表是分页而不是截断：会话列表跟着服务端那个不透明的 keyset 游标走，配一个「加载更多」控件；更早的一页消息按时间插入而不是追加，因为这一栏里本来就有比最新一页消息更早的 activity 步骤。
- Artifact 用哪个渲染器由服务端的 [`../renderers.py`](../renderers.py) 决定。sequence、alignment、genome、Molfile/SMILES 与 LaTeX 的解析在 [`scientific_renderers.js`](scientific_renderers.js) 里；table、image、PDF、HTML 与 text 的展示主要在 `app.js` 中组合。HTML preview 里的脚本是可以执行的：它们跑在不带 `allow-same-origin` 的沙箱 iframe 中，碰不到主应用的 origin，但这个 preview 并不是一个无脚本的渲染器。
- 前端刻意手写 HTML/CSS/JavaScript。请保持 DOM ID 和事件名稳定，离线静态契约测试和浏览器冒烟测试都是照着它们写的。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`app.js`](app.js) | 客户端的全部逻辑都在这一个文件里：本地化与主题、同源 API wrapper、WebSocket 生命周期、路由、Dashboard/Workspace 状态、流式消息、执行控制、Workbench 投影、Artifact、Notebook 与 Timeline、模型、计划、审阅、Skill、会话包与设置。 |
| [`favicon.js`](favicon.js) | 浏览器支持时用 WebCodecs 逐帧播放 GIF favicon，标签页隐藏时暂停，不支持时回退到静态 GIF。 |
| [`login.html`](login.html) | 团队模式登录页（`OPENAI4S_TEAM_MODE`）。只用内联样式——脚本必须外链，因为 CSP 只哈希 `index.html` 的内联脚本。两种模式下都在 `/login` 提供；守卫把未登录的浏览器 303 到这里。 |
| [`login.js`](login.js) | 登录页脚本：POST `/api/v1/auth/login`，把失败原因一句话展示出来；已登录或团队模式关闭时直接跳回首页。 |
| [`replay.html`](replay.html) | 只读回放查看页（M2-3）——guest 的全部界面，也是成员的快速一瞥。在登录守卫之后的 `/replay` 提供；只用内联样式，脚本外链以过 CSP。 |
| [`replay.js`](replay.js) | 拉取 `GET /api/v1/sessions/{id}/replay`（现场构建的脱敏 web-share view.json），把消息与科学 cell 渲染成朴素的转录稿。 |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | 打包的 favicon 源文件：动画解码的帧来自它，静态回退图标也是它。 |
| [`index.html`](index.html) | Dashboard、对话 Workspace、composer、右侧 dock、dialog 与设置的可访问 DOM 骨架。它在首屏绘制前应用主题，并引用静态脚本与样式。 |
| [`scientific_renderers.js`](scientific_renderers.js) | 零依赖的 sequence/MSA、genome、Molfile/SMILES、LaTeX 解析与辅助函数，外加渲染器描述符校验。它们只产出普通数据、绝不产出 HTML，DOM 由 `app.js` 依据这些记录构建；一层薄薄的 UMD 包装让 Node 契约测试能直接导入同一个文件。通用的 table/image/PDF/HTML/text 展示仍留在 `app.js` 中。 |
| [`style.css`](style.css) | 整套视觉系统：明暗 token、字体、Dashboard/Workspace 布局、Activity 与 Artifact 组件、dialog、可访问性以及移动端断点。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `share/` | 独立的只读分享查看器（`share.html`/`share.js`/`share.css`），由 relay 隧道的 ShareRouter 提供，与主单页应用分开，而且是自成一体的，不是主应用的裁剪版。它的外壳只加载 `share.js` 和 `share.css`，别的一概不加载：没有 WebSocket，不与 `app.js` 共享任何状态，Markdown 和 CSV 也由它自带的极简渲染器处理，而不是 `scientific_renderers.js`。ShareRouter 的资产白名单确实放行了 `scientific_renderers.js` 和自带的 3Dmol，好让更完整的查看器可以取用，但当前这套外壳两者都不请求。 |
| `vendor/` | 从上游取来的压缩版 3Dmol 运行时和字体资源。3Dmol 是客户端里唯一的第三方 JavaScript，而且只有在打开分子 Artifact 时才由 `app.js` 动态注入。自带的那份如果加载不上，Artifact 直接退回纯文本展示，不存在 CDN 回退（见 [`app.js`](app.js) 里注入 `3Dmol-min.js` 的那处 script 标签）。把它们当作上游的、逐字节敏感的资产：不参与格式化，本 README 也不逐个文件说明。 |

## 验证

在仓库根目录运行离线 UI 契约：

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

只要改动涉及交互或流式传输，就必须过一遍真实浏览器：启动 `./start.sh`，把真正的 WebSocket 流程跑一遍。另见 [Server 包总览](../README_zh.md)与 [Web 应用指南](../../../docs/webapp.md)。
