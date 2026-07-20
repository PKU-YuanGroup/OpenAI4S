# skills/bioprobench/kernel.py
import json
import re
import ast
import os
import sys
from itertools import combinations

# ==========================================
# 0. Dynamic Script Mounting
# ==========================================
# Mount the script directory so we can dynamically load original scripts
current_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = os.path.join(current_dir, "Scripts")
if os.path.exists(script_dir) and script_dir not in sys.path:
    sys.path.append(script_dir)

# ==========================================
# 1. Strict Dependency Guard (OpenAI4S Rule)
# ==========================================
try:
    import numpy as np
    from tqdm import tqdm
    import nltk
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    from nltk.translate.meteor_score import meteor_score
    from rouge_score import rouge_scorer
    from keybert import KeyBERT
    from sentence_transformers import SentenceTransformer, util
    from sklearn.metrics.pairwise import cosine_similarity
    from sklearn.metrics import brier_score_loss

    # Download required NLTK data safely
    nltk.download('punkt', quiet=True)
    nltk.download('wordnet', quiet=True)
except ImportError as e:
    raise ImportError(
        f"BioProBench requires additional science dependencies: {e}. "
        "Please ensure numpy, tqdm, nltk, scikit-learn, rouge_score, keybert, and sentence_transformers are installed in your kernel."
    )

# ==========================================
# 2. Shared Helper Functions
# ==========================================
def extract_binary_answer(generated_str):
    if '</think>' in generated_str:
        generated_str = generated_str.split("</think>")[-1]
    if '[/INST]' in generated_str:
        generated_str = generated_str.split("[/INST]")[-1]

    pattern = r"\[ANSWER_START\](.*?)\[ANSWER_END\]"
    match = re.search(pattern, generated_str, re.DOTALL)
    
    if match:
        answer = match.group(1).strip()
    else:
        generated_str = generated_str.strip()
        answer = generated_str.split('\n')[-1].strip()

    if 'True' in answer or 'true' in answer:
        return True
    elif 'False' in answer or 'false' in answer:
        return False
    else:
        raise ValueError("Invalid or unrecognized answer format")

# ==========================================
# 3. Task Evaluation Modules
# ==========================================

# --- Task: REA-ERR (Reasoning) ---
def evaluate_step_reasoning_model(result_path):
    llm_judge, total, failed = 0, 0, 0
    with open(result_path, 'r') as f:
        data = json.load(f)
    for item in data:
        if "LLM_judge" in item:
            total += 1
            try:
                is_correct = extract_binary_answer(item['LLM_judge'])
                llm_judge += int(is_correct)
            except Exception:
                failed += 1
                continue
    acc = llm_judge / (total - failed) * 100 if (total - failed) > 0 else 0
    fail_rate = failed / total * 100 if total > 0 else 0
    return {"Consistency": acc, "Failure_Rate": fail_rate, "Total": total, "Failed": failed}

# --- Task: ERR (Correction) ---
def evaluate_correction_task(output_file_path):
    preds, gts = [], []
    failed, total = 0, 0
    with open(output_file_path, 'r') as f:
        data = json.load(f)
    for item in data:
        total += 1
        try:
            pred = extract_binary_answer(item["generated_response"])
            preds.append(pred)
            gts.append(item["is_correct"])
        except Exception:
            failed += 1
            
    TP = sum((p is False and g is False) for p, g in zip(preds, gts))
    FP = sum((p is False and g is True) for p, g in zip(preds, gts))
    FN = sum((p is True and g is False) for p, g in zip(preds, gts))
    
    accuracy = sum(p == g for p, g in zip(preds, gts)) / len(preds) if preds else 0
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN) if (TP + FN) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1, "failed_rate": failed / total if total else 0}

# --- Task: GEN (Generation) ---
def evaluate_protocolgen_model(result_path):
    # Lazy Loading: Initialize models only when evaluating GEN to save memory
    EMBEDDING_MODEL = SentenceTransformer('all-mpnet-base-v2')
    KEYWORD_MODEL = KeyBERT(SentenceTransformer('all-MiniLM-L6-v2'))
    SIMILARITY_THRESHOLD = 0.7

    def extract_text_response(text):
        return text.split('</think>')[-1].strip().split('</Structure>')[-1].strip().split('[ANSWER_START]')[-1].strip().split('[ANSWER_END]')[0].strip()

    with open(result_path, 'r') as f:
        json_list = json.load(f)

    bleu_list, meteor_list, rouge1_list, rouge2_list, rougel_list = [], [], [], [], []
    kw_precision_list, kw_recall_list, kw_f1_list = [], [], []
    sr_list, rp_list = [], []
    failed = 0

    for item in json_list:
        ref = item['output']
        gen = item['generated_response']
        if gen is None:
            failed += 1
            continue

        gen_clean = extract_text_response(gen)
        
        if isinstance(ref, list):
            gen_steps = [step.strip() for step in gen_clean.split('\n') if step.strip()]
            ref_embeds = EMBEDDING_MODEL.encode(ref)
            gen_embeds = EMBEDDING_MODEL.encode(gen_steps)
            matched_refs, matched_gens = set(), set()
            
            for i, ref_vec in enumerate(ref_embeds):
                for j, gen_vec in enumerate(gen_embeds):
                    if cosine_similarity([ref_vec], [gen_vec])[0][0] >= SIMILARITY_THRESHOLD:
                        matched_refs.add(i)
                        break
            for i, gen_vec in enumerate(gen_embeds):
                for j, ref_vec in enumerate(ref_embeds):
                    if cosine_similarity([gen_vec], [ref_vec])[0][0] >= SIMILARITY_THRESHOLD:
                        matched_gens.add(i)
                        break
            
            sr = len(matched_refs) / len(ref) if ref else 1.0
            rp = 1.0 - ((len(gen_steps) - len(matched_gens)) / len(gen_steps)) if gen_steps else 1.0
            sr_list.append(sr)
            rp_list.append(rp)
            ref_text = " ".join(ref)
        else:
            ref_text = str(ref)

        gen_text = str(gen_clean)
        
        # NLTK Metrics
        ref_tokens = nltk.word_tokenize(ref_text.lower())
        gen_tokens = nltk.word_tokenize(gen_text.lower())
        bleu_list.append(sentence_bleu([ref_tokens], gen_tokens, weights=(0.5, 0.5), smoothing_function=SmoothingFunction().method1))
        meteor_list.append(meteor_score([ref_tokens], gen_tokens))
        
        # Rouge
        scorer = rouge_scorer.RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=True)
        r_scores = scorer.score(ref_text, gen_text)
        rouge1_list.append(r_scores["rouge1"].fmeasure)
        rouge2_list.append(r_scores["rouge2"].fmeasure)
        rougel_list.append(r_scores["rougeL"].fmeasure)

        # Keyword
        ref_kw = set([kw for kw, _ in KEYWORD_MODEL.extract_keywords(ref_text, top_n=64)])
        gen_kw = set([kw for kw, _ in KEYWORD_MODEL.extract_keywords(gen_text, top_n=64)])
        if ref_kw and gen_kw:
            intersection = ref_kw & gen_kw
            kw_p = len(intersection) / len(gen_kw)
            kw_r = len(intersection) / len(ref_kw)
            kw_f1_list.append(2 * kw_p * kw_r / (kw_p + kw_r + 1e-8))
            kw_precision_list.append(kw_p)
            kw_recall_list.append(kw_r)
        else:
            kw_f1_list.append(0.0)
            kw_precision_list.append(0.0)
            kw_recall_list.append(0.0)

    return {
        "BLEU": np.mean(bleu_list), "METEOR": np.mean(meteor_list), 
        "ROUGE-L": np.mean(rougel_list), "KW_F1": np.mean(kw_f1_list),
        "Step_Recall": np.mean(sr_list) if sr_list else None,
        "Redundancy_Penalty": np.mean(rp_list) if rp_list else None,
        "Failed_Rate": failed / len(json_list) if len(json_list) else 0
    }

# --- Task: ORD (Sorting) ---
def evaluate_sorting_predictions(output_file_path):
    with open(output_file_path, 'r') as f:
        data = json.load(f)
    preds, gts = [], []
    failed, total = 0, 0
    
    for item in data:
        total += 1
        try:
            generated_str = item["generated_response"].split("</think>")[-1]
            match = re.findall(r"\[ANSWER_START\](.*?)\[ANSWER_END\]", generated_str, re.DOTALL)
            indices = ast.literal_eval(match[-1].strip())
            preds.append([item["wrong_steps"][i] for i in indices])
            gts.append(item["correct_steps"])
        except Exception:
            failed += 1

    exact_match = sum([gt == pr for gt, pr in zip(gts, preds)]) / len(gts) if gts else 0
    
    total_pairs, concordant_pairs = 0, 0
    for gt, pr in zip(gts, preds):
        gt_rank = {step: i for i, step in enumerate(gt)}
        pr_rank = {step: i for i, step in enumerate(pr)}
        for a, b in combinations(gt_rank.keys(), 2):
            if (gt_rank[a] - gt_rank[b]) * (pr_rank[a] - pr_rank[b]) > 0:
                concordant_pairs += 1
            total_pairs += 1
    kendall_tau = (2 * concordant_pairs - total_pairs) / total_pairs if total_pairs > 0 else 0
    
    return {"Exact_Match": exact_match, "Kendall_Tau": kendall_tau, "Failed_Rate": failed / total if total else 0}

# --- Task: PQA (Question Answering) ---
def evaluate_predictions(output_file_path):
    with open(output_file_path, 'r') as f:
        data = json.load(f)
    accs, cfds = [], []
    failed, total = 0, 0
    
    for item in data:
        total += 1
        try:
            generated_str = item['generated_response']
            if '</think>' in generated_str:
                generated_str = generated_str.split("</think>")[-1]
            content = re.search(r"\[ANSWER_START\](.*?)\[ANSWER_END\]", generated_str, re.DOTALL).group(1).strip()
            
            parts = content.split('&') if '&' in content else [' '.join(content.split(' ')[:-1]), content.split(' ')[-1]]
            answer = parts[0].strip()
            confidence = int(re.search(r"\d+", parts[-1]).group())
            
            cfds.append(min(confidence, 100))
            accs.append(1 if answer == item['answer'] else 0)
        except Exception:
            failed += 1

    accuracy = sum(accs) / len(accs) if accs else 0
    brier = brier_score_loss(accs, np.array(cfds) / 100) if accs else None
    return {"Accuracy": accuracy, "Brier_Score": brier, "Failed_Rate": failed / total if total else 0}


# ==========================================
# 4. Agent Evaluation Entry Point
# ==========================================
def run_bioprobench_eval(task_name: str, response_file_path: str):
    """
    Main entry point for OpenAI4S agents to evaluate model responses against BioProBench.
    """
    if not os.path.exists(response_file_path):
        return {"error": f"File not found: {response_file_path}"}
        
    eval_dispatch = {
        'REA-ERR': evaluate_step_reasoning_model,
        'ERR': evaluate_correction_task,
        'GEN': evaluate_protocolgen_model,
        'ORD': evaluate_sorting_predictions,
        'PQA': evaluate_predictions
    }
    
    if task_name not in eval_dispatch:
        return {"error": f"Unknown task: {task_name}. Supported tasks: {list(eval_dispatch.keys())}"}
        
    try:
        metrics = eval_dispatch[task_name](response_file_path)
        return {
            "task": task_name,
            "status": "success",
            "metrics": metrics
        }
    except Exception as e:
        return {"error": f"Evaluation failed during execution: {str(e)}"}

# ==========================================
# 5. Agent Inference & Script Bridging
# ==========================================
def _load_script(module_name: str, file_name: str):
    """Dynamically load script files to bypass Python's import hyphen restrictions."""
    import importlib.util
    file_path = os.path.join(script_dir, file_name)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Original script {file_name} not found in {script_dir}")
    
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def get_task_prompt(sample: dict, task_name: str) -> str:
    """Agent interface to fetch the standardized prompt for a specific sample."""
    try:
        prompt_format = _load_script("prompt_format", "prompt_format.py")
        return prompt_format.generate_user_prompt(sample, task_name)
    except Exception as e:
        return f"Error loading prompt format: {e}"

def run_llm_judge_evaluation(sample: dict, api_key: str, base_url: str = "https://api.deepseek.com", model_name: str = "deepseek-chat") -> str:
    """Agent interface for dynamic LLM Judge evaluation."""
    try:
        from openai import OpenAI
        llm_judge = _load_script("llm_judge", "LLM-as-a-judge_for_REA-ERR.py")
        
        # Safely override the global client initialized in the script with the real Agent credentials
        llm_judge.client = OpenAI(api_key=api_key, base_url=base_url)
        
        prompt = llm_judge.generate_user_prompt(sample)
        judgment = llm_judge.generate_response(prompt, model_name)
        return judgment
    except ImportError:
        return "Error: The 'openai' package is required to run the LLM Judge."
    except Exception as e:
        return f"Error during LLM Judge execution: {e}"

def trigger_batch_inference(task_name: str, test_file_path: str, mode: str = "API", model_name: str = "o3-mini", api_key: str = None) -> dict:
    """Agent interface to trigger full dataset batch inference."""
    try:
        if mode == "API":
            if not api_key:
                return {"error": "API mode requires an api_key provided securely by the agent."}
            from openai import OpenAI
            api_inf = _load_script("api_inf", "generate_response.py")
            
            # Dynamically override the script's global configs
            api_inf.API_KEY = api_key
            api_inf.MODEL_NAME = model_name
            api_inf.TASK_NAME = task_name
            api_inf.TEST_FILE_PATH = test_file_path
            api_inf.OUTPUT_FILE = f"{task_name}_test_{model_name}_api.json"
            api_inf.client = OpenAI(api_key=api_key, base_url=api_inf.BASE_URL)
            
            api_inf.main()
            return {"status": "success", "message": f"API batch inference for {task_name} completed. Saved to {api_inf.OUTPUT_FILE}."}
            
        elif mode == "Local":
            local_inf = _load_script("local_inf", "generate_response_local.py")
            
            # Dynamically override the script's global configs
            local_inf.MODEL_NAME = model_name
            local_inf.TASK_NAME = task_name
            local_inf.TEST_FILE_PATH = test_file_path
            local_inf.OUTPUT_FILE = f"{task_name}_test_{model_name}_local.json"
            
            local_inf.main()
            return {"status": "success", "message": f"Local batch inference for {task_name} completed. Saved to {local_inf.OUTPUT_FILE}."}
        else:
            return {"error": "Unknown mode. Choose 'API' or 'Local'."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
