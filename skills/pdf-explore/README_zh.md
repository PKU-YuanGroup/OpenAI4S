# PDF Explore Skill

这个渐进披露 Skill 支持从大到无法常驻对话 context 的 PDF 做跨章节导航与抽取。Sidecar 解析/缓存页面并可 fan out 有界 Host LLM call；结果仍受 parser/OCR 质量影响，figure 或 layout 需要视觉核验。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 在 page parsing、outline、relevance scan、map、structured extraction、细节 figure crop、OCR mode、cache 与 cost-aware fan-out 之间选择，并说明何时直接读页更简单。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：解析 local path/Artifact ID；以 `pdf_pages` 解析并缓存逐页 text/image；构造 nonce-guarded prompt；通过并行 `host.llm` map/scan/extract 页面；构建 outline；安全截断；并用 `pdf_scan_cost` 汇总 usage。 |

## 直属子目录

无。

可选 PDF library/OCR 工具必须存在于活动内核。抽取文本可能丢失视觉结构；关键 label 与 value 应对照渲染页面/crop 检查。
