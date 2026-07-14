# fair-esm2 Skill

本目录保存通过外部 `fair-esm` package 使用 Meta ESM-2 embedding、masked-language-model score、mutation effect 与 contact prediction 的渐进披露 recipe。

它不捆绑 checkpoint，也不保证 CPU/GPU 容量。模型 likelihood/contact 输出属于计算预测，需要针对任务验证。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明 checkpoint 选择、alphabet/batch conversion、representation layer、pooled/per-residue embedding、mask-based mutation scoring、contact、批处理、内存与模型限制。 |

## 直属子目录

无。
