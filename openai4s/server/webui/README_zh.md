# Web UI（浏览器界面）

[English](README.md)

本目录是标准库 Gateway 在 `/static/` 提供的零依赖浏览器客户端。它没有 bundler 或编译步骤：源码 checkout 中的修改在浏览器刷新后即可看到；安装后的 wheel 则提供包内副本。UI 消费 REST 读/写端点和一个 WebSocket 事件流，但不拥有规范会话状态。

## 运行时职责

- Dashboard 与 Workspace shell 提供项目、会话、对话、模型选择、计划/审阅、审批、Artifact、Notebook、Timeline、Context、Security、分支/恢复、Skill 与设置界面。
- `app.js` 保存浏览器投影和临时交互状态。核心 Workbench 投影使用显式 sanitizer，且不保留供应商 wire payload 或原始工具参数；部分 Settings 和 legacy `innerHTML` 路径仍需要完整的 escaping 审计。
- WebSocket 事件更新流式文本、Cell、activity、执行所有权及 Workbench 读模型；REST 用于有界读取和显式 mutation。
- Artifact renderer 先由服务端 [`../renderers.py`](../renderers.py) 选择。Sequence、alignment、genome、Molfile/SMILES 与 LaTeX 解析 helper 位于 [`scientific_renderers.js`](scientific_renderers.js)；table、image、PDF、HTML 与 text 展示主要由 `app.js` 组合。HTML preview 中的脚本可以在不含 `allow-same-origin` 的 sandboxed iframe 中执行；它们不在主应用 origin 执行，但该 preview 不是无脚本 renderer。
- 前端刻意使用手写 HTML/CSS/JavaScript。请保持稳定 DOM ID 与事件名，因为离线静态契约和浏览器 smoke test 依赖它们。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`app.js`](app.js) | 主客户端：本地化/主题、同源 API wrapper、WebSocket 生命周期、路由、Dashboard/Workspace 状态、流式消息、执行控制、Workbench 投影、Artifact、Notebook/Timeline、模型、计划、审阅、Skill、会话包与设置。 |
| [`favicon.js`](favicon.js) | 在支持时用 WebCodecs 播放 GIF favicon，在隐藏 tab 中暂停，并在不支持时回退为静态 GIF。 |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | 打包的动画/静态 favicon 来源。 |
| [`index.html`](index.html) | Dashboard、对话 Workspace、composer、右侧 dock、dialog 与设置的可访问 DOM shell；首屏绘制前应用主题并引用静态脚本/样式。 |
| [`scientific_renderers.js`](scientific_renderers.js) | UMD、零依赖、仅产出数据的 parser/helper，支持 sequence/MSA、genome、Molfile/SMILES、LaTeX 与 renderer-descriptor 校验，也可由 Node 契约测试导入。通用 table/image/PDF/HTML/text 展示仍在 `app.js` 中。 |
| [`style.css`](style.css) | 完整响应式视觉系统：明暗 token、字体、Dashboard/Workspace 布局、Activity 与 Artifact 组件、dialog、可访问性及移动端断点。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| `vendor/` | Vendored 的压缩 3Dmol runtime 与字体资源；视为上游/字节敏感资产，不参与格式化，本 README 不逐文件说明。 |

## 验证

在仓库根目录运行离线 UI 契约：

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

影响交互或流式传输的修改还应启动 `./start.sh`，在真实浏览器/WebSocket 流程中验证。另见 [Server 包总览](../README_zh.md)与 [Web 应用指南](../../../docs/webapp.md)。
