# PDF Explore Skill

用来对付大到无法常驻对话 context 的 PDF。用 `read_file` 附上的页面，过一轮就会被丢掉，于是一个横跨多节的问题会变成反复重读同一批页；这个 Skill 改为在 Python 内核里把文档解析一次，文本就留在那里不走了。先找到需要的章节，再从里面把要的东西取出来，其余的留在磁盘上。Sidecar 缓存解析好的页面，然后对这些页面并行发出有界的 `host.llm` 调用。模型能看到的，只是文本层或 OCR 能读出来的那些内容，不会更多。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 教你挑对工具：先看 outline，再按需要用相关性 scan、逐页 map、结构化抽取，或者在要从图里读数时渲染高 dpi 再裁剪。同时说明扫描件模式、页面缓存、一次 fan-out 的成本，以及什么时候直接读页比这一整套都省事。 |
| [`kernel.py`](kernel.py) | 可选 sidecar；它与内核的 `__main__` 共享命名空间，所以所有名字都带 `pdf_` 前缀。`pdf_resolve` 把路径或 Artifact ID 落成本地文件，`pdf_pages` 解析并缓存逐页文本与页面渲染图。在此之上，`pdf_outline` 构建目录（PDF 自带 outline 就用自带的，没有才让模型来做），`pdf_scan` 按查询给页面排序，`pdf_map` 逐页总结，`pdf_extract` 按 JSON Schema 从每页抽取记录，全部走并行 `host.llm` 调用，并受批量上限约束。页面文本是不可信输入：每次调用都用随机 nonce 作为分隔符构造 prompt，形似标签的页面文本在插入前会被中和，过长的页面按显式标记截断。跑完之后用 `pdf_scan_cost` 汇总 token 用量。 |

可选的 PDF 与 OCR 库必须存在于当前内核中。抽取出来的文本丢掉了页面的视觉结构，因此凡是要据以下结论的 label 和数值，都应该回到渲染出的页面或它的 crop 上核对一遍。
