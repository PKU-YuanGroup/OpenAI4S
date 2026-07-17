# BioProBench: Biological Protocol Understanding and Reasoning

> **Citation & Authorship**
> This skill integrates BioProBench, the first large-scale, integrated multi-task benchmark for biological protocol understanding and reasoning.
> If you use this benchmark to evaluate agents or models, please cite the original work:
> 
> ```bibtex
> @article{liu2025bioprobench,
>   title={BioProBench: Comprehensive Dataset and Benchmark in Biological Protocol Understanding and Reasoning},
>   author={Liu, Yuyang and Lv, Liuzhenghao and Zhang, Xiancheng and Yuan, Li and Tian, Yonghong},
>   journal={ICML},
>   url={https://github.com/YuyangSunshine/bioprotocolbench/tree/main},
>   year={2026}
> }
> ```
> *(Note: This benchmark serves as the foundational evaluation environment for downstream physical execution agents like BioProAgent.)*

## Description
Biological protocols are the fundamental bedrock of reproducible and safe life science research. While LLMs show strong general capabilities, wet-lab environments demand high-precision procedural logic. 

This skill provides a robust framework to evaluate an agent's capability across **5 core reasoning tasks**:
1. **PQA (Protocol Question Answering):** Comprehension of reagent dosages, parameters, and instructions.
2. **ORD (Step Ordering):** Global and local procedural hierarchy reconstruction.
3. **ERR (Error Correction):** Identifying safety or validity risks in modified steps.
4. **GEN (Protocol Generation):** Synthesis of procedural steps.
5. **REA-ERR (Experimental Reasoning):** Deep reasoning over error constraints.

## Capabilities & Data
The full testing dataset contains approximately 5,000 structured instances across these five fine-grained tasks. Researchers should download the official dataset from Hugging Face (`https://huggingface.co/BioProBench`) to their local working directory before running the evaluation.

## Agent Usage Instructions
To evaluate a model's generated responses against the BioProBench dataset, use the `run_bioprobench_eval` function provided in the persistent Python kernel.

### Python API
```python
import bioprobench.kernel as bpb

# Execute the evaluation by passing the task name and the path to the model's output JSON
results = bpb.run_bioprobench_eval(
    task_name='PQA', # Options: 'PQA', 'ORD', 'ERR', 'GEN', 'REA-ERR'
    response_file_path='/path/to/your/model_responses.json'
)

# Submit the standardized metrics back to the Engine
host.submit_output(results)