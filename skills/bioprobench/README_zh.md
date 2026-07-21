# BioProBench Skill

面向 BioProBench 的渐进披露 recipe。这是一个覆盖真实湿实验方案的五任务基准：问答、步骤排序、错误纠正、方案生成，以及由 LLM 裁判评判的错误推理。基准数据本身不随仓库分发，这里只放了一份两条记录的样例，用来说明文件应该长什么样。

在读别的内容之前，最该知道的一点是：评测吃的是**一个已经把标准答案与模型回复合并在同一条记录里**的文件，而不是「模型输出文件 + 另一份答案表」。如果只喂纯模型输出，每条记录都会静默解析失败，指标全部归零，而返回信封依然写着 `success`。真正能区分这两种情况的字段是 `Failed_Rate`。此外 sidecar 在模块级就把整套科学依赖全部导入，并在首次导入时下载 NLTK 语料，所以在 OpenAI4S 默认精简 venv 里它根本导入不了。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 完整的 recipe：五个任务分别返回哪些指标键（各任务大小写并不统一）、评测实际要求的逐任务记录 schema，以及解析器期待的 `[ANSWER_START] … [ANSWER_END]` 包裹格式。然后是真实签名——`run_bioprobench_eval` 要么返回 `{task, status, metrics}` 信封，要么返回一个只有单键的 `{"error": …}`，因此收尾要按 `"error" in result` 分支；`host.submit_output` 的 completion bullets 是必填参数；再加上 `trigger_batch_inference`、`get_task_prompt` 和 `run_llm_judge_evaluation`。最后如实交代每条路径各自需要哪些第三方包、会走哪些网络调用。 |
| [`kernel.py`](kernel.py) | 可导入的 sidecar。每个任务一个评测函数，统一挂在 `run_bioprobench_eval` 的分发表下；另外还有一层桥接，按文件路径动态加载 `Scripts/` 里的上游脚本（它们的文件名带连字符，无法正常 import），在调用 `main()` 之前覆写其模块级配置。第三方依赖按任务惰性导入，模块本身在任何环境下都能导入成功。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`Scripts/`](Scripts/) | 上游 BioProBench 的推理与 LLM 裁判脚本，原样沿用。由 `kernel.py` 按路径加载，不作为 package 导入。 |
| [`data/`](data/) | 一份两条记录的 PQA 样例，用来说明记录的确切形状。完整的约 5,000 条基准数据需另行从 Hugging Face 下载。 |
