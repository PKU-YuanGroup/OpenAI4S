# DiffDock 参考资料

只有一份文件，主 recipe 里的单 complex 路径不够用时才会读它。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`workflows.md`](workflows.md) | 批量 CSV 怎么写、一个片段库怎么跑完，以及只有序列、没有受体结构时如何让 DiffDock 先用 ESMFold 折叠受体。还讲了跨 complex 读 confidence 的问题：logit 只在同一个 complex 内部校准过，跨配体不可比，因此不能拿它当亲和力排序。 |
