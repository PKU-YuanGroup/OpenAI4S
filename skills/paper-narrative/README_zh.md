# Paper Narrative Skill

三个渐进披露 figure Skill 里最外层的一个：审阅论文稿和整套 figure 讲出来的故事，并重新安排它。输入就是工作本身，一位“责任编辑”角色的评审会给出 hook 是否立得住、从 hook 到应用的叙事弧、哪些 panel 放错了 figure、还缺哪些 panel、哪些材料该砍掉。它给的是编辑意见，既不是科学证据，也不是接收概率的预测。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明什么时候加载（写稿或改稿阶段，早于 `figure-composer`），以及整个流程：从 abstract 和图注推导出 brief，以责任编辑的身份评审整套图，按叙事弧、figure 之间的搬迁、缺失 panel 和删除清单动手，把每张留下的 figure 的 claim 交给 `figure-composer`，最后对新一版重新评审。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`pn_sdk` 取得的 `host` 句柄不会因为内核里这个名字被重新绑定而失效；`paper_brief_schema` 与 `narrative_review_schema` 是两份结构化输出的 schema；`derive_paper_brief` 用一次强制走工具的 `host.llm` 调用，从 abstract 加图注里提取 pitch、vision 和逐图 claim；`narrative_review_task` 构造针对整套图的责任编辑 prompt。 |

模型给出的缺失 panel 建议，只是指出一项值得做的分析。它不等于这项分析已经做过。
