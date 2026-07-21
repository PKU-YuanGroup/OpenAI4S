# ESMFold2 参考资料

这两份文件只有当任务真的用到比较专门的模型接口时，主 recipe 才会读进来。它们描述的都是上游 API，具体有没有、长什么样，取决于装的是哪个版本。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`design-hook.md`](design-hook.md) | ESMFold2-Experimental 变体为设计任务开出的梯度钩子，以及为什么不能在这些模型上打开 fused kernel backend。这部分明确标注为实验性指导，不保证 API 稳定。 |
| [`esmc.md`](esmc.md) | ESMC 的加载方式。它写到的四个接口里，只有两个配了可直接跑的代码，并标注了返回的 tensor shape：MLM logits 加 hidden state，以及 layer 60 上的稀疏自编码器特征。另外两个只有文字说明，没有代码，也没有 shape。zero-shot 突变打分讲的是论文里的 Alg 14；残基接触预测给出 P@L-LR 基准数字，并让你去 `esm.models.esmc` 模块找回归头。 |
