# BioProBench Data

完整的 BioProBench 测试集约有 5,000 条结构化实例，覆盖五个任务。为了保持仓库轻量，这里不托管完整数据——请先从 Hugging Face（<https://huggingface.co/BioProBench>）下载到工作目录，再开始正式评测。

这里随附的是一份两条记录的样例，重点在于 schema 而不是内容。评测是从**携带模型回复的同一批记录里**读取标准答案的，因此一份纯模型输出的文件会得零分，却仍然返回 `success`。这份样例就是「一条形状正确的记录长什么样」的参照。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`sample_pqa_output.json`](sample_pqa_output.json) | 两条 PQA 记录，形状与 `run_bioprobench_eval` 的要求完全一致：基准字段（`question`、`answer`、`choices`、`type`、`id`）并排合并了一个 `generated_response`。`answer` 就是评测比对用的标准答案；每个 `generated_response` 都演示了必需的 `[ANSWER_START] <choice> & <confidence> [ANSWER_END]` 包裹格式，前面可以带一段可选的 `</think>` 前缀。 |
