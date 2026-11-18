# frontend/src/islands

[English](README.md)

F-18 命令式孤岛。3Dmol 懒注入 script 标签（只取自带副本；删-CDN 安全注释留在标签旁）、图片标注器、dock Viewer / 版本、应用同源 Ketcher `/ketcher` iframe（embeddable 头，不加 sandbox）、只加载应用自建 `application/pdf` blob 的无沙箱 PDF iframe，以及惰性的初始 HTML iframe。HTML 获得 scoped grant 后可在已验证的另一回环源运行脚本。不改 `stores/`，也不改 `compat/window-exports.ts` 标记区以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`annot.ts`](annot.ts) | 图片标注器、pin 状态、作曲框 chip（app.js:8965-8993, 9149-9429）。 |
| [`annot.test.ts`](annot.test.ts) | `annotationStatus` 映射；held pin；`openAnnotations`；删除一条后评论列表在重绘的 chip 上重新打开。 |
| [`dom.ts`](dom.ts) | `el`（再导出 artifacts 的同名函数）/ `$` / lucide 子集 / `ghostIconBtn`。 |
| [`frames.ts`](frames.ts) | 惰性 HTML iframe 策略、无沙箱的 PDF blob frame（`applyPdfFrame`：Chromium 不在任何沙箱 frame 里运行 PDF 查看器）、回环源与 grant 路径检查；应用同源 Ketcher 与剪贴板权限。 |
| [`frames.test.ts`](frames.test.ts) | iframe sandbox 属性、派生的回环源、grant 路径检查及应用同源 Ketcher。 |
| [`host.ts`](host.ts) | 用 `isReady` 调 window；`t()` 回退。 |
| [`index.ts`](index.ts) | `bootIslands` / `installIslands`。覆盖 F-05 占位。 |
| [`install.test.ts`](install.test.ts) | 安装后契约名字通过 `isReady`。 |
| [`ketcher.ts`](ketcher.ts) | `openKetcher` iframe 打开 `/ketcher`（app.js:10834）。 |
| [`locator.ts`](locator.ts) | PDF / HTML locator 评论（app.js:10835-10897）。 |
| [`mol.ts`](mol.ts) | `_molTeardown` + `molecule` 懒注入（app.js:9610-9673）。每个视图分别持有有界结构读取与场景，侧栏与全屏可以共存。关闭预览会取消其读取并清除其模型；共享的离屏 WebGL2 context 仅在最后一个视图关闭后释放。读取失败显示原因、重试与确切版本下载。控件：样式与着色（默认、按链、按 B 因子，附图例；文件不支持的方案置灰并说明原因；配体保持元素配色）、重置视角（回到首次适配时的方向与缩放）、点击残基切换标签、清除标签，以及显示 PDB ID 与标题（从已读取的文本解析）、链和原子数的信息行。 |
| [`mol.test.ts`](mol.test.ts) | 无 3Dmol 静态 import；无活 CDN URL；自带 src；每个容器只有最新且仍在页面上的结构创建 viewer。覆盖独立的侧栏/全屏生命周期、按视图清理、延迟读取时稳定选择兼容桥、所有存活视图同步主题，以及共享脚本只加载一次。ART-6 控件：信息行只用一次读取、重置回首次适配再重新适配、点击标签的切换与清除、按链/B 因子着色及图例与表面、小分子的方案置灰、缺少相应 3Dmol API 时不加标签也不保存视角，以及每个控件的中英文案。 |
| [`mol-copy.ts`](mol-copy.ts) | ART-6 的 `molT` 文案表（中/英）：着色方案、重置视角、标签、信息行与鼠标提示（自带的 3Dmol 中 Ctrl+拖动才是平移，不是 Shift）。原有的 tag 与样式键仍在 F-07 字典中。 |
| [`mol-facts.ts`](mol-facts.ts) | 结构查看器的纯函数：解析 PDB HEADER/TITLE（遇到第一条坐标记录即停）、聚合物链与 B 因子范围、残基标签/键/锚点、链配色与蓝-白-红 B 因子配色，以及各表示方式与着色方案对应的 3Dmol 样式。不碰 DOM，也不依赖 3Dmol。 |
| [`mol-facts.test.ts`](mol-facts.test.ts) | 用真实的 2NYD 与 AlphaFold 记录测试头部解析（续行、CRLF、短行、占位 ID、长度上限），以及信息与方案可用性、残基标签/键/锚点、配色映射，并确认默认方案保持原有样式。 |
| [`viewer.ts`](viewer.ts) | Dock Viewer 界面、编辑器、版本历史及记录来源面板（app.js:9430-9609）。检索来源面板把被隐藏的响应摘要显示为“已隐藏”（并说明它与可核验的文件摘要不同），显式列出冲突字段与被规范化的旧别名，不再静默择值；记录来源面板同时显示明确的核验未知与元数据未提供状态。 |
| [`viewer.test.ts`](viewer.test.ts) | 文本编辑门禁、元数据/来源渲染、双语核验状态合同，以及检索来源的隐藏摘要/别名/冲突合同。 |
