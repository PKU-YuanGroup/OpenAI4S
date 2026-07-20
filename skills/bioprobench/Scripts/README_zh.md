# bioprobench 脚本

BioProBench 评测的可运行部分。

- [`prompt_format.py`](prompt_format.py) —— 从数据集构造任务提示。
- [`generate_response.py`](generate_response.py) —— 通过托管 API 收集模型回答。
- [`generate_response_local.py`](generate_response_local.py) —— 同上，但面向本地部署的模型。
- [`LLM-as-a-judge_for_REA-ERR.py`](LLM-as-a-judge_for_REA-ERR.py) —— 用模型做裁判为 REA-ERR 任务打分。
