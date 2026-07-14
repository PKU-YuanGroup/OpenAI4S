# Retrosynthesis Aspirin 示例

本目录演示如何从 committed AiZynthFinder-shaped route tree 与 illustrative annotation 确定性渲染结果。它不声称发生了 live search、文献核验、vendor check、合成或 assay。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 读取 committed route/annotation fixture，通过父目录 sidecar 规范化/排序 route，重新生成 HTML/Markdown，并报告使用 RDKit 还是 placeholder SVG depiction。 |
| [`aspirin_routes.json`](aspirin_routes.json) | 五个 AiZynthFinder-shaped aspirin route tree，含 molecule/reaction node、stock flag、score 与 metadata，用于确定性 normalization/ranking。 |
| [`aspirin_annotations.json`](aspirin_annotations.json) | 确定性演示 route/molecule/reaction prose、risk、condition strategy 与 next step；明确只是 illustrative，不是实验证据。 |
| [`aspirin_retrosynthesis.html`](aspirin_retrosynthesis.html) | 生成的 self-contained interactive dashboard，含 ranked route、structure/placeholder、route card 与 knowledge/tree view。 |
| [`aspirin_retrosynthesis_report.md`](aspirin_retrosynthesis_report.md) | 生成的 fixture analyst report，列出 ranked route、molecule brief 与 review note。 |

## 直属子目录

无。
