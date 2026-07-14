# OpenFold3 Skill

OpenFold3 的渐进披露 recipe。OpenFold3 是 AlQuraishi Lab 用 PyTorch 复现的 AlphaFold3，权重开放。这份 recipe 说明模型怎么装、怎么跑，但一样都不带：没有 OpenFold3 代码，没有数据库，没有参数，也没有准备好的环境。它和另外三份 co-folding recipe 有两点不同。OpenFold3 根本不读 FASTA，一次查询是一个 JSON 对象，还要过一遍严格的 schema 校验。另外，它的 MSA 服务开关默认是开的，也就是说除非明确关掉，序列会离开本机。

蛋白质、核酸、配体、模板与加速器这几条路实际能不能走通，取决于装的是哪个上游版本、资产齐不齐。聚合 confidence 文件要按它本来的含义读。`sample_ranking_score` 只在同一次运行的若干样本之间排序，所以哪怕这次查询压根没有真实答案，其中最好的那个样本照样排在第一位。`has_clash: 0.0` 说的是原子没有重叠，那是模型画出来的几何的性质。这些数字都是模型在给自己打分。复合物到底存不存在，它们够不着。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先讲查询 JSON，因为写错它的结构是最先撞上的失败：一个 `queries` 字典，每条链一个 `molecule_type`，蛋白质/DNA/RNA 给 `sequence`，配体给 `smiles` 或 `ccd_codes`，整个对象要过一遍 pydantic 校验，多余的键直接被拒。接着是那两个不关就一直开着的开关——`--use-msa-server` 会把序列 POST 给 `api.colabfold.com`，`--use-templates` 还额外要求能连上 `data.rcsb.org`——所以离线或出网受限的环境必须把两个都显式设成 `false`。然后是 Hugging Face 上要先过访问申请的权重下载、默认的 DeepSpeed attention kernel 以及 DeepSpeed 缺失时改用 cuEquivariance 的回退办法、聚合 confidence 文件里的数值该长什么样、一张针对导入报错和显存不足的排查表，最后是上游许可。 |
