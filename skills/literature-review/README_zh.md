# Literature Review Skill

这个渐进披露 Skill 提供 evidence-first 文献综合流程。Sidecar 可以查询公共学术 metadata 并核验 identifier，但网络响应、索引覆盖、撤稿状态与全文访问仍是外部且随时间变化的条件。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义需求 framing、retrieve-before-write grounding、DOI/source 核验、retraction/null-result 处理、比较式综合、证据校准、citation placement 与最终 prose 检查。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：取得可重新绑定的 Host SDK/contact；执行有界 Crossref/OpenAlex/DOI HTTP lookup；提取/quote/verify DOI；扩展 citation graph；做最小 HTML decode；运行确定性 `style_pass` prose lint。 |

## 直属子目录

无。

Lookup 成功不等于全文核验，lookup 失败也不能证明论文不存在。最终 claim 必须继续以实际检索到的 primary source 为依据。
