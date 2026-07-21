# scvi-tools Skill

在自己的数据上训练 scVI 和 scANVI：一个做过批次校正、建立在原始 count 之上的隐空间，在它之上做半监督的标签迁移，再加贝叶斯差异表达。两个模型都只认原始整数 UMI count，别的都不行，所以这份 recipe 有相当篇幅是在讲怎么别在中途把它们弄没。加载这个 Skill 时可以挂入一个很小的兼容 sidecar，而且只挂进本地的分析内核。其余的东西都得自己备齐：scvi-tools 本身、PyTorch、数据、训练好的模型，还有 GPU。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 做任何归一化之前，先把原始 count 存进一个 layer：把 log 归一化后的数据喂给 `setup_anndata`，scVI 的负二项似然不会报错，只会安静地给出垃圾结果。然后训练 scVI，在它之上叠 scANVI 做标签迁移，再把 embedding、解码出来的表达和差异表达结果取回来。它还钉住了两处会让旧代码翻车的 API 变更——`use_gpu=` 在 1.x 里被删掉，改为 `accelerator="gpu", devices=1`；`differential_expression` 现在默认 `mode="vanilla"`，而这种模式的结果列里根本没有 `lfc_*`，也没有 `proba_de`——此外还有远程计算的写法，以及写 `.h5ad` 时被 Arrow 字符串搞崩的那一处。 |
| [`kernel.py`](kernel.py) | 可选的 sidecar，只有一个函数 `h5ad_safe_obs`：复制一份 observation 表，把 index 和字符串类的列转成 HDF5 能安全承载的形式，之后再序列化成 `.h5ad` 就不会在这上面翻车。 |

收敛情况、批次校正效果、迁移过来的标签，以及差异表达的结论，都必须拿手上的真实数据核对一遍。`pred_cell_type` 是分类器的猜测，不是标注；差异表达表描述的是拟合出来的生成模型，而不是在细胞上量出来的结果。另外，recipe 躺在磁盘上并不代表环境里就装了兼容版本的 scvi-tools。
