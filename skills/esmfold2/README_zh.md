# ESMFold2 Skill

这个渐进披露 recipe 覆盖外部 Biohub ESMFold2/ESMFold2-Fast co-folding 模型与 ESMC 蛋白语言模型。本目录不捆绑代码、weights、Hugging Face 访问或 GPU 环境。

必须在活动环境检查 model/backend/version 可用性。Structure、mutation、contact 与 interpretability 输出属于计算结果，不能表述为经实验验证。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Entity/input format、single-sequence 与 MSA mode、kernel backend/diffusion step、输出/confidence 解读、性能、模型选择及上游许可/weight 来源的主 recipe。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需读取的 experimental design-hook 说明与 ESMC language-model recipe。 |
