# Literature Review Skill

以证据为先的文献工作流程：先检索，再动笔。你写出来的 DOI，要么真的指向一篇说了你所说内容的论文，要么就是编造，而这两者只需几秒钟就能分辨——所以这份 recipe 要求核验发生在工具调用的记录里，而不是回复里的一句话，哪怕那篇论文你闭着眼都能背出来。Sidecar 可以查询公共学术 API 并核验 identifier，但索引覆盖到哪里、某篇论文有没有被标记撤稿、全文能不能拿到，都是外部条件，而且会随时间变化。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先弄清对方到底在问什么；每一条论断都落在检索到的文献上；DOI 靠查不靠记；撤稿与“根本没有这篇论文”这种诚实答案怎么给；综述是比较而不是罗列；结论的把握程度要和证据强度匹配；引用就地内联；以及保存之前的文字检查。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`lr_sdk` 取得的 `host` 句柄不会因为内核里这个名字被重新绑定而失效，`litrev_contact` 在能拿到用户邮箱时把它放进 polite-pool 的 User-Agent。`litrev_get` 与 `litrev_head` 是底下那层有界的 HTTP 请求（429 重试一次，出错返回 `None`），`quote_doi_path` 把 DOI 编码进请求路径，`crossref_year` 再把年份读回来。在这之上：`verify_dois`、`crossref_lookup`、`search_openalex` 负责解析与检索，`expand_citations` 沿引文网络前后各走一步，`extract_dois` 和 `html_decode` 把 DOI 从正文里抠出来。`style_pass` 是对成稿跑的正则 lint，特意不含任何 LLM 调用：稿子里引着检索来的第三方文本，而一条让 Agent 照做的自由文本修改建议，本身就是一条注入通道。 |

查得到不等于核验过全文；查不到也不能证明这篇论文不存在。最终的论断必须落在真正检索到的原始文献上。
