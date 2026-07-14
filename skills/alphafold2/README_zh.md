# AlphaFold2 Skill

本目录保存通过 ColabFold 使用 AlphaFold2/AlphaFold2-Multimer 的渐进披露 recipe。Loader 起初只暴露 Skill 名称与摘要；任务需要 MSA-backed 单体/多聚体折叠及 confidence/self-consistency 审阅时，Agent 才读取 [`SKILL.md`](SKILL.md)。

本目录不捆绑 AlphaFold 代码、weights、环境或运行中的服务。执行依赖 ColabFold、合适算力与模型参数；选择公共 MSA 路径时还会把序列发送到外部 ColabFold MMseqs2 服务。GPU metadata 只是需求信号，不代表 GPU 已可用。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明 ColabFold 输入约定、AF2/Multimer 命令、输出/confidence 解读、设计 self-consistency、批处理、限制、外部服务披露与第三方许可的 recipe。 |

## 直属子目录

无。
