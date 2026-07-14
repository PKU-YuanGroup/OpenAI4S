# Retrosynthesis Aspirin 示例

演示当输入是一份 commit 好的 AiZynthFinder 格式路线树和一组示意性注释时，确定性渲染出来是什么样子。这背后没有跑过真实搜索，没有查过文献，没有查过供应商，也没有做过合成或实验测定。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 读入两份 fixture，交给上一级目录的 sidecar 做路线规范化与排序，再重新生成 HTML 和 Markdown 报告。它会打印这次的结构图是 RDKit 画的，还是占位 SVG。 |
| [`aspirin_routes.json`](aspirin_routes.json) | 五棵 AiZynthFinder 导出格式的 aspirin 路线树，带分子节点、反应节点、库存标记、score 与 metadata，足以让规范化和排序每次都跑出同样的结果。 |
| [`aspirin_annotations.json`](aspirin_annotations.json) | dashboard 上展示的演示注释：路线、分子、反应的说明文字，以及风险、条件策略和后续步骤。这些是为填满面板而写的示意文本，不是实验证据。 |
| [`aspirin_retrosynthesis.html`](aspirin_retrosynthesis.html) | 生成的 dashboard，自包含且可交互：排序后的路线、分子结构图或占位图、route card，以及知识图谱与树视图。 |
| [`aspirin_retrosynthesis_report.md`](aspirin_retrosynthesis_report.md) | 为同一份 fixture 生成的分析报告：排序后的路线、分子简介、审阅注意事项。 |
