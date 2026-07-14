# Protein Mutation Enhancement Skill

这个渐进披露 Skill 定义确定性的迭代蛋白 mutation 工作流，可合并 sequence、structure、property 与 function score。Sidecar 构建和排序 candidate record；它本身不运行 ESM/folding/assay 模型，也不能证明 gain of function。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义输入契约、single/double/higher-order library 构建、外部 score 生成、thresholded ranking、round continue/stop 规则、默认值与验证要求。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：校验 sequence/mutation notation；规范化并应用 variant；确定性枚举 library；计算简单 property-conservation score；读取 score table/写 FASTA；合并/规范化/排序 metric；运行 thresholded selection round；建议下一轮 position；持久化 ranked JSON。 |

## 直属子目录

无。

内置 property score 是 heuristic component，不是功能预测器。最终 candidate 需要独立计算与实验验证。
