# 科学内核环境

[English](README.md)

`openai4s setup` 就是从这四份 conda 规格创建出四个可选任务环境的。它们属于执行平面；
标准库控制平面不依赖其中任何一个。

## 文件

| 文件 | 职责 |
| --- | --- |
| `python.yml` | Python 3.11，用于常规数据分析与绘图。带上了单细胞栈（scanpy、anndata、leidenalg、umap-learn）、scikit-learn 和常用数值库，rdkit 与 fair-esm 由 pip 装入。 |
| `phylo.yml` | Python 3.11，面向系统发育与生物信息学。除 biopython、dendropy、ete3 外，还装了建树流程要用的命令行工具：mafft、iqtree、fasttree、trimal。 |
| `r.yml` | R 4.5.3，以及独立 R 内核通道所需的包：tidyverse、data.table、ggplot2、knitr/rmarkdown、jsonlite。只用 conda-forge，不引入 bioconda。 |
| `struct.yml` | Python 3.13，面向结构生物学与蛋白质语言模型。biotite、biotraj 来自 conda；torch 与 fair-esm 走 pip，有意选可移植的 CPU 构建。想要 GPU 加速就换成 conda 的 pytorch 构建。 |

选定哪个环境，决定的是 worker 跑在哪个解释器上；路由、权限、存储与 Host RPC 仍然归
daemon 所有。新的可选依赖包应该写进这些文件，而不是变成零依赖核心里的硬导入。
