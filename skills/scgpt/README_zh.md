# scGPT Skill

scGPT 是一个在单细胞表达数据上预训练的 transformer：用外部 checkpoint 做单细胞 embedding、细胞类型注释和基因层面的表示。想要的是一个基础模型对数据集的看法，就用它；想要的是在自己的 count 上拟合出来的生成模型，那是 `scvi-tools`（scVI / scANVI）。scGPT 跑起来需要的东西，本目录一概不提供：checkpoint、vocabulary、`AnnData`、GPU 环境都得自己准备。

数据这一头 recipe 替你查不了，得自己确认：checkpoint 的目录结构对不对、基因名能不能和 vocabulary 对上、counts 是按什么方式预处理的、batch 元数据和标签是不是真如你所想。还有一点：无论是 zero-shot 还是微调之后的注释，都只是模型输出，不是真实标签。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | scGPT 的 checkpoint 是一个原始目录（`args.json`、`best_model.pt`、`vocab.json`），不是 Hugging Face 仓库，所以加载器要的是文件系统路径，repo id 喂不进去。`adata.var` 里的基因名必须和 checkpoint 的 vocabulary 对得上；对不上的会被悄悄丢掉，所以在读结果之前先把 `gene_col` 核一遍。`embed_data` 把逐细胞的 embedding 留在 `.obsm["X_scGPT"]`，注释从这里继续。此外还有批处理与资源需求、远程计算路径，以及真正会咬人的两个默认值：`use_fast_transformer` 默认为 `True`，它走的是未必 import 得起来的 FlashAttention 路径；torchtext 的 `Vocab` shim 过期时，报出来的是属性缺失，而不是一次干脆的失败。 |
