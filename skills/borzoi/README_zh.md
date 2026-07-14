# Borzoi Skill

这个渐进披露 recipe 覆盖 Borzoi DNA-to-functional-track 预测，用于 locus track 与 ref/alt 变异比较。它说明如何操作外部 PyTorch 模型；本目录不捆绑模型 runtime 或 checkpoint。

执行取决于兼容 package、已下载 weights、track metadata 与较大 GPU 显存。预测 track delta 只是优先级排序的模型证据，不是因果或临床验证。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 记录模型加载、524-kb one-hot 输入、人/鼠 head、输出 bin/track、reverse-complement、ref/alt scoring、metadata 对齐、显存限制与许可来源。 |

## 直属子目录

无。
