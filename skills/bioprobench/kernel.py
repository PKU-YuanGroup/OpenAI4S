# skills/bioprobench/kernel.py
import json
import re
import ast
import os
from itertools import combinations

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
# 4. Agent Entry Point
# ==========================================
def run_bioprobench_eval(task_name: str, response_file_path: str):
    """
    Main entry point for OpenAI4S agents to evaluate model responses against BioProBench.
    
    Args:
        task_name (str): One of 'PQA', 'ORD', 'ERR', 'GEN', 'REA-ERR'.
        response_file_path (str): The absolute path to the generated JSON responses.
    
    Returns:
        dict: A dictionary containing the metrics for the specified task.
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