# scGPT Skill

这个渐进披露 recipe 覆盖利用外部 scGPT checkpoint 进行单细胞 embedding、annotation 与 gene representation。本目录不包含 checkpoint、vocabulary、`AnnData` 或 GPU 环境。

必须验证 checkpoint layout、vocabulary alignment、count preprocessing、batch metadata 与 label validation。Zero-shot/fine-tuned annotation 仍是模型输出，不是 ground truth。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 记录 raw checkpoint/vocabulary 加载、gene-token 对齐、embedding/annotation 流程、批处理、`AnnData` 输出位置、资源需求及生物/技术限制。 |

## 直属子目录

无。
