# BioProBench Scripts

来自 BioProBench 上游项目（<https://github.com/YuyangSunshine/bioprotocolbench>）的推理与 LLM 裁判脚本，此处仅在每个文件顶部加了一行来源说明注释，其余原样沿用。

它们按独立命令行脚本的方式写成，配置项都是模块级常量，并不是一个库。因此 `kernel.py` 通过 `importlib` 按文件路径加载它们而不是 import——其中一个文件名带连字符，本来也无法作为模块导入——并在调用 `main()` 之前覆写这些常量。每个脚本运行时都会产生网络或 GPU 开销：要么发出对外 API 调用，要么加载 HuggingFace 权重。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`prompt_format.py`](prompt_format.py) | `generate_user_prompt(sample, task_name)`——逐任务的标准化 prompt，其中包含评测端随后要解析的 `[ANSWER_START] … [ANSWER_END]` 输出约定。唯一一个没有副作用、也不依赖第三方包的脚本。 |
| [`generate_response.py`](generate_response.py) | 面向 OpenAI 兼容端点的批量推理，带重试与周期性 checkpoint。就地给每条记录加上 `generated_response` 键，已有该键的记录会跳过。需要 `openai` 和 `tqdm`。 |
| [`generate_response_local.py`](generate_response_local.py) | 同样的批量循环，改为通过固定在 `cuda:0` 的 `text-generation` pipeline 调用本地 HuggingFace 因果语言模型。需要 `transformers`、`torch` 和 GPU。 |
| [`LLM-as-a-judge_for_REA-ERR.py`](LLM-as-a-judge_for_REA-ERR.py) | 评判 REA-ERR 回复与参考错误描述是否一致，并把结论写入 `LLM_judge` 键，供 REA-ERR 评测函数读取。默认使用 `deepseek-chat`，需要 `openai`。 |
