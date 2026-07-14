# Web UI（浏览器界面）

[English](README.md)

浏览器客户端放在这里，由标准库 Gateway 在 `/static/` 下提供。这里没有 npm、没有打包器、没有编译步骤：源码 checkout 里改一行，刷新浏览器就能看到；安装后的 wheel 提供的则是包内副本。`index.html` 本身只加载 `favicon.js`、`scientific_renderers.js` 和 `app.js`，但它并不是页面上唯一的脚本来源。有一个第三方库仍会进到页面里：打开分子 Artifact 时，`app.js` 会在运行时注入 3Dmol，先取 `vendor/` 下自带的那一份；这一份加载失败时，它会改从 `https://3Dmol.org/build/3Dmol-min.js` 再取一次——对一个平时只跑在 loopback 上的应用来说，这是一次真实的对外请求。如果 CDN 上的那份也失败，Artifact 就退回成纯文本展示。客户端通过 REST 读取和写入，并跟随一条 WebSocket 事件流。它手里只有会话状态的投影，规范状态始终在服务端。

## 运行时职责

- 整个产品由两层壳撑起来：Dashboard 和对话 Workspace。两者一起提供项目、会话、对话、模型选择、计划/审阅、审批、Artifact、Notebook、Timeline、Context、Security、分支/恢复、Skill 与设置界面。
- `app.js` 保存浏览器侧的投影和临时交互状态。核心 Workbench 投影都经过显式的净化处理，既不保留 provider 的原始报文，也不保留工具调用的原始参数。部分 Settings 路径和遗留的 `innerHTML` 代码还没有做完整的转义审计。
- WebSocket 事件驱动流式文本、Cell、activity、执行所有权以及 Workbench 的读模型；REST 负责有界读取和显式写入。
- Artifact 用哪个渲染器由服务端的 [`../renderers.py`](../renderers.py) 决定。sequence、alignment、genome、Molfile/SMILES 与 LaTeX 的解析在 [`scientific_renderers.js`](scientific_renderers.js) 里；table、image、PDF、HTML 与 text 的展示主要在 `app.js` 中组合。HTML preview 里的脚本是可以执行的：它们跑在不带 `allow-same-origin` 的沙箱 iframe 中，碰不到主应用的 origin，但这个 preview 并不是一个无脚本的渲染器。
- 前端刻意手写 HTML/CSS/JavaScript。请保持 DOM ID 和事件名稳定，离线静态契约测试和浏览器冒烟测试都是照着它们写的。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`app.js`](app.js) | 客户端的全部逻辑都在这一个文件里：本地化与主题、同源 API wrapper、WebSocket 生命周期、路由、Dashboard/Workspace 状态、流式消息、执行控制、Workbench 投影、Artifact、Notebook 与 Timeline、模型、计划、审阅、Skill、会话包与设置。 |
| [`favicon.js`](favicon.js) | 浏览器支持时用 WebCodecs 逐帧播放 GIF favicon，标签页隐藏时暂停，不支持时回退到静态 GIF。 |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | 打包的 favicon 源文件：动画解码的帧来自它，静态回退图标也是它。 |
| [`index.html`](index.html) | Dashboard、对话 Workspace、composer、右侧 dock、dialog 与设置的可访问 DOM 骨架。它在首屏绘制前应用主题，并引用静态脚本与样式。 |
| [`scientific_renderers.js`](scientific_renderers.js) | 零依赖的 sequence/MSA、genome、Molfile/SMILES、LaTeX 解析与辅助函数，外加渲染器描述符校验。它们只产出普通数据、绝不产出 HTML，DOM 由 `app.js` 依据这些记录构建；一层薄薄的 UMD 包装让 Node 契约测试能直接导入同一个文件。通用的 table/image/PDF/HTML/text 展示仍留在 `app.js` 中。 |
| [`style.css`](style.css) | 整套视觉系统：明暗 token、字体、Dashboard/Workspace 布局、Activity 与 Artifact 组件、dialog、可访问性以及移动端断点。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `vendor/` | 从上游取来的压缩版 3Dmol 运行时和字体资源。3Dmol 是客户端里唯一的第三方 JavaScript，而且只有在打开分子 Artifact 时才由 `app.js` 动态注入。自带的那份如果加载不上，这次注入会先回退到 `3Dmol.org` 的 CDN，都失败才退回纯文本展示（见 [`app.js`](app.js) 里注入 `3Dmol-min.js` 的两处 script 标签）。把它们当作上游的、逐字节敏感的资产：不参与格式化，本 README 也不逐个文件说明。 |

## 验证

在仓库根目录运行离线 UI 契约：

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

只要改动涉及交互或流式传输，就必须过一遍真实浏览器：启动 `./start.sh`，把真正的 WebSocket 流程跑一遍。另见 [Server 包总览](../README_zh.md)与 [Web 应用指南](../../../docs/webapp.md)。
