# skills/bioprobench/kernel.py
import ast
import contextlib
import importlib
import importlib.util
import json
import os
import re
import sys
import traceback
from itertools import combinations

# ==========================================
# 0. Script directory (loaded privately, never via sys.path)
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = os.path.join(current_dir, "Scripts")

# Private namespace prefix for the vendored Scripts/*.py modules. Loading them
# under this prefix keeps generic names such as ``prompt_format`` out of the
# shared persistent kernel's module table.
_SCRIPT_NS = "_openai4s_bioprobench_scripts"

# ==========================================
# 1. Lazy per-task dependency resolution
# ==========================================
# Importing this module must always succeed: none of the heavy scientific
# dependencies below ship in the default OpenAI4S venv, and PQA/ORD/ERR/REA-ERR
# do not need most of them. Each task resolves only its own requirements and
# raises a clear error naming the packages it is actually missing.

# task -> (pip-installable distribution names, importable module names)
_TASK_REQUIREMENTS = {
    "PQA": (("numpy", "scikit-learn"), ("numpy", "sklearn")),
    "ORD": ((), ()),
    "ERR": ((), ()),
    "REA-ERR": ((), ()),
    "GEN": (
        (
            "numpy",
            "scikit-learn",
            "nltk",
            "rouge_score",
            "keybert",
            "sentence_transformers",
        ),
        ("numpy", "sklearn", "nltk", "rouge_score", "keybert", "sentence_transformers"),
    ),
}


def _require(task_name: str):
    """Import the third-party modules `task_name` needs, or raise a clear error."""
    dists, modules = _TASK_REQUIREMENTS.get(task_name, ((), ()))
    missing = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
    if missing:
        raise ImportError(
            f"BioProBench task {task_name} requires {', '.join(dists)}; "
            f"missing from this kernel: {', '.join(missing)}. "
            "Install them into the kernel environment first."
        )


def _ensure_nltk_resources(nltk):
    """Fetch the tokenizer/wordnet data GEN needs. Best effort, never fatal here.

    nltk >= 3.8.2 moved `word_tokenize` onto the `punkt_tab` resource; older
    releases only ship `punkt`. Ask for whichever the installed nltk resolves.
    """

    def obtain(resource_path, package):
        try:
            nltk.data.find(resource_path)
            return True, None
        except Exception:
            pass
        try:
            return bool(nltk.download(package, quiet=True)), None
        except Exception as exc:  # network down, read-only NLTK_DATA, ...
            return False, type(exc).__name__

    warnings = []
    # Either tokenizer resource is enough: punkt_tab exists only on newer nltk,
    # punkt only satisfies word_tokenize on older nltk. Try both, warn if
    # neither lands.
    tokenizer_errors = []
    tokenizer_ok = False
    for resource_path, package in (
        ("tokenizers/punkt_tab", "punkt_tab"),
        ("tokenizers/punkt", "punkt"),
    ):
        ok, err = obtain(resource_path, package)
        tokenizer_ok = tokenizer_ok or ok
        if not ok:
            tokenizer_errors.append(f"{package}{f' ({err})' if err else ''}")
    if not tokenizer_ok:
        warnings.append("tokenizer data unavailable: " + ", ".join(tokenizer_errors))

    ok, err = obtain("corpora/wordnet", "wordnet")
    if not ok:
        warnings.append(f"wordnet{f' ({err})' if err else ''}")
    return warnings


# ==========================================
# 2. Shared Helper Functions
# ==========================================
def extract_binary_answer(generated_str):
    if "</think>" in generated_str:
        generated_str = generated_str.split("</think>")[-1]
    if "[/INST]" in generated_str:
        generated_str = generated_str.split("[/INST]")[-1]

    pattern = r"\[ANSWER_START\](.*?)\[ANSWER_END\]"
    match = re.search(pattern, generated_str, re.DOTALL)

    if match:
        answer = match.group(1).strip()
    else:
        generated_str = generated_str.strip()
        answer = generated_str.split("\n")[-1].strip()

    if "True" in answer or "true" in answer:
        return True
    elif "False" in answer or "false" in answer:
        return False
    else:
        raise ValueError("Invalid or unrecognized answer format")


_TRUE_LABELS = {"true", "t", "yes", "y", "1"}
_FALSE_LABELS = {"false", "f", "no", "n", "0"}


def normalize_bool_label(value):
    """Coerce a ground-truth label to a real bool.

    Datasets encode `is_correct` as a JSON bool, as 0/1, or as a string. Left
    un-normalised these compare unequal under `is True` / `is False`, which
    silently collapses precision/recall/F1 to zero while accuracy stays right.
    Unrecognised encodings raise, so bad data is counted as *failed* rather
    than scored as a confident wrong answer.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_LABELS:
            return True
        if token in _FALSE_LABELS:
            return False
    raise ValueError(f"Unrecognized boolean label: {value!r}")


def _mean_or_none(values):
    """Mean of `values`, or None when empty — never NaN (NaN is not valid JSON)."""
    if not values:
        return None
    return sum(values) / len(values)


# ==========================================
# 3. Task Evaluation Modules
# ==========================================


# --- Task: REA-ERR (Reasoning) ---
def evaluate_step_reasoning_model(result_path):
    llm_judge, judged, failed = 0, 0, 0
    with open(result_path, "r") as f:
        data = json.load(f)
    total_items = len(data)
    for item in data:
        if "LLM_judge" in item:
            judged += 1
            try:
                is_correct = extract_binary_answer(item["LLM_judge"])
                llm_judge += int(is_correct)
            except Exception:
                failed += 1
                continue
    acc = llm_judge / (judged - failed) * 100 if (judged - failed) > 0 else 0
    fail_rate = failed / judged * 100 if judged > 0 else 0
    return {
        "Consistency": acc,
        "Failure_Rate": fail_rate,
        # `Total`/`Failure_Rate`/`Consistency` are over the *judged* subset only.
        # `Total_Items`/`Unjudged`/`Coverage` expose the true denominator so an
        # incomplete judge pass cannot hide behind a flattering Consistency.
        "Total": judged,
        "Failed": failed,
        "Total_Items": total_items,
        "Judged": judged,
        "Unjudged": total_items - judged,
        "Coverage": judged / total_items if total_items else 0,
    }


# --- Task: ERR (Correction) ---
def evaluate_correction_task(output_file_path):
    preds, gts = [], []
    failed, total = 0, 0
    with open(output_file_path, "r") as f:
        data = json.load(f)
    for item in data:
        total += 1
        try:
            pred = extract_binary_answer(item["generated_response"])
            gt = normalize_bool_label(item["is_correct"])
        except Exception:
            # Append nothing: a half-appended item would shift every later
            # prediction against the wrong label for the rest of the run.
            failed += 1
            continue
        preds.append(pred)
        gts.append(gt)

    # The positive class is "this protocol step is wrong" (label False).
    TP = sum((not p) and (not g) for p, g in zip(preds, gts))
    FP = sum((not p) and g for p, g in zip(preds, gts))
    FN = sum(p and (not g) for p, g in zip(preds, gts))

    accuracy = sum(p == g for p, g in zip(preds, gts)) / len(preds) if preds else 0
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN) if (TP + FN) > 0 else 0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    )

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "failed_rate": failed / total if total else 0,
    }


# --- Task: GEN (Generation) ---
def evaluate_protocolgen_model(result_path):
    # Lazy dependency resolution: GEN is the only task needing the torch-scale
    # stack, and the only one needing NLTK corpora off the network.
    _require("GEN")
    import nltk
    from keybert import KeyBERT
    from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu
    from nltk.translate.meteor_score import meteor_score
    from rouge_score import rouge_scorer
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity

    nltk_warnings = _ensure_nltk_resources(nltk)

    # Lazy Loading: Initialize models only when evaluating GEN to save memory
    EMBEDDING_MODEL = SentenceTransformer("all-mpnet-base-v2")
    KEYWORD_MODEL = KeyBERT(SentenceTransformer("all-MiniLM-L6-v2"))
    SIMILARITY_THRESHOLD = 0.7

    def extract_text_response(text):
        return (
            text.split("</think>")[-1]
            .strip()
            .split("</Structure>")[-1]
            .strip()
            .split("[ANSWER_START]")[-1]
            .strip()
            .split("[ANSWER_END]")[0]
            .strip()
        )

    with open(result_path, "r") as f:
        json_list = json.load(f)

    bleu_list, meteor_list, rouge1_list, rouge2_list, rougel_list = [], [], [], [], []
    kw_precision_list, kw_recall_list, kw_f1_list = [], [], []
    sr_list, rp_list = [], []
    failed = 0

    for item in json_list:
        ref = item["output"]
        gen = item["generated_response"]
        if gen is None:
            failed += 1
            continue

        gen_clean = extract_text_response(gen)

        if isinstance(ref, list):
            gen_steps = [step.strip() for step in gen_clean.split("\n") if step.strip()]
            ref_embeds = EMBEDDING_MODEL.encode(ref)
            gen_embeds = EMBEDDING_MODEL.encode(gen_steps)
            matched_refs, matched_gens = set(), set()

            for i, ref_vec in enumerate(ref_embeds):
                for j, gen_vec in enumerate(gen_embeds):
                    if (
                        cosine_similarity([ref_vec], [gen_vec])[0][0]
                        >= SIMILARITY_THRESHOLD
                    ):
                        matched_refs.add(i)
                        break
            for i, gen_vec in enumerate(gen_embeds):
                for j, ref_vec in enumerate(ref_embeds):
                    if (
                        cosine_similarity([gen_vec], [ref_vec])[0][0]
                        >= SIMILARITY_THRESHOLD
                    ):
                        matched_gens.add(i)
                        break

            sr = len(matched_refs) / len(ref) if ref else 1.0
            rp = (
                1.0 - ((len(gen_steps) - len(matched_gens)) / len(gen_steps))
                if gen_steps
                else 1.0
            )
            sr_list.append(sr)
            rp_list.append(rp)
            ref_text = " ".join(ref)
        else:
            ref_text = str(ref)

        gen_text = str(gen_clean)

        # NLTK Metrics
        ref_tokens = nltk.word_tokenize(ref_text.lower())
        gen_tokens = nltk.word_tokenize(gen_text.lower())
        bleu_list.append(
            sentence_bleu(
                [ref_tokens],
                gen_tokens,
                weights=(0.5, 0.5),
                smoothing_function=SmoothingFunction().method1,
            )
        )
        meteor_list.append(meteor_score([ref_tokens], gen_tokens))

        # Rouge
        scorer = rouge_scorer.RougeScorer(
            ["rouge1", "rouge2", "rougeL"], use_stemmer=True
        )
        r_scores = scorer.score(ref_text, gen_text)
        rouge1_list.append(r_scores["rouge1"].fmeasure)
        rouge2_list.append(r_scores["rouge2"].fmeasure)
        rougel_list.append(r_scores["rougeL"].fmeasure)

        # Keyword
        ref_kw = set(
            [kw for kw, _ in KEYWORD_MODEL.extract_keywords(ref_text, top_n=64)]
        )
        gen_kw = set(
            [kw for kw, _ in KEYWORD_MODEL.extract_keywords(gen_text, top_n=64)]
        )
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

    # np.mean([]) is NaN, which json.dumps emits as bare `NaN` — not valid JSON.
    # Report None instead and let the entry point mark the status honestly.
    metrics = {
        "BLEU": _mean_or_none(bleu_list),
        "METEOR": _mean_or_none(meteor_list),
        "ROUGE-L": _mean_or_none(rougel_list),
        "KW_F1": _mean_or_none(kw_f1_list),
        "Step_Recall": _mean_or_none(sr_list),
        "Redundancy_Penalty": _mean_or_none(rp_list),
        "Failed_Rate": failed / len(json_list) if len(json_list) else 0,
    }
    if nltk_warnings:
        metrics["NLTK_Data_Warnings"] = nltk_warnings
    return metrics


# --- Task: ORD (Sorting) ---
def _gt_index_order(wrong_steps, correct_steps):
    """Express `correct_steps` as an ordering of positions in `wrong_steps`.

    Ranking by step *text* loses pairs whenever a protocol repeats a step
    string, so ranks are keyed by index. Duplicate texts are matched to their
    first still-unused occurrence.
    """
    if len(wrong_steps) != len(correct_steps):
        raise ValueError(
            f"correct_steps has {len(correct_steps)} entries but wrong_steps has "
            f"{len(wrong_steps)}"
        )
    remaining = {}
    for i, step in enumerate(wrong_steps):
        remaining.setdefault(step, []).append(i)
    order = []
    for step in correct_steps:
        bucket = remaining.get(step)
        if not bucket:
            raise ValueError("correct_steps is not a permutation of wrong_steps")
        order.append(bucket.pop(0))
    return order


def evaluate_sorting_predictions(output_file_path):
    with open(output_file_path, "r") as f:
        data = json.load(f)
    preds, gts = [], []
    pred_orders, gt_orders = [], []
    failed, total = 0, 0

    for item in data:
        total += 1
        try:
            generated_str = item["generated_response"].split("</think>")[-1]
            match = re.findall(
                r"\[ANSWER_START\](.*?)\[ANSWER_END\]", generated_str, re.DOTALL
            )
            indices = ast.literal_eval(match[-1].strip())
            wrong_steps = item["wrong_steps"]
            correct_steps = item["correct_steps"]
            if not isinstance(indices, (list, tuple)):
                raise ValueError(f"predicted order is not a list: {indices!r}")
            indices = [int(i) for i in indices]
            # Validate here, not in the Kendall-tau loop below: an index that is
            # dropped, repeated or out of range used to raise a KeyError outside
            # the per-item handler and abort the whole evaluation, discarding
            # every other item's result.
            if sorted(indices) != list(range(len(wrong_steps))):
                raise ValueError(
                    f"predicted order {indices} is not a permutation of "
                    f"range({len(wrong_steps)})"
                )
            gt_order = _gt_index_order(wrong_steps, correct_steps)
        except Exception:
            failed += 1
            continue
        preds.append([wrong_steps[i] for i in indices])
        gts.append(correct_steps)
        pred_orders.append(indices)
        gt_orders.append(gt_order)

    exact_match = sum([gt == pr for gt, pr in zip(gts, preds)]) / len(gts) if gts else 0

    total_pairs, concordant_pairs = 0, 0
    for gt_order, pred_order in zip(gt_orders, pred_orders):
        gt_rank = {idx: i for i, idx in enumerate(gt_order)}
        pr_rank = {idx: i for i, idx in enumerate(pred_order)}
        try:
            item_pairs, item_concordant = 0, 0
            for a, b in combinations(gt_rank.keys(), 2):
                if (gt_rank[a] - gt_rank[b]) * (pr_rank[a] - pr_rank[b]) > 0:
                    item_concordant += 1
                item_pairs += 1
        except Exception:
            # Belt-and-braces: never let one item abort the aggregate.
            failed += 1
            continue
        concordant_pairs += item_concordant
        total_pairs += item_pairs
    kendall_tau = (
        (2 * concordant_pairs - total_pairs) / total_pairs if total_pairs > 0 else 0
    )

    return {
        "Exact_Match": exact_match,
        "Kendall_Tau": kendall_tau,
        "Failed_Rate": failed / total if total else 0,
    }


# --- Task: PQA (Question Answering) ---
_CONFIDENCE_TOKEN = re.compile(r"^(100|\d{1,2})\s*%?$")


def _split_answer_and_confidence(content):
    """Split `[ANSWER_START] … [ANSWER_END]` content into (answer, confidence).

    The `&` form is the one prompt_format asks the model for; the whitespace
    form is a fallback. Both validate the trailing token the same way, so a
    confidence is never fabricated from a digit run: a response that does not
    carry a well-formed 0-100 score is rejected and lands in Failed_Rate rather
    than being scored as a confident answer.
    """
    if "&" in content:
        parts = content.split("&")
        # Only the LAST field is the confidence; an answer may itself contain
        # "&" (reagent names such as "Tris & EDTA"), and prompt_format requires
        # the answer to match a choice exactly, so truncating at the first "&"
        # would score a correct answer as wrong.
        answer = "&".join(parts[:-1]).strip()
        token = parts[-1].strip()
        if not _CONFIDENCE_TOKEN.match(token):
            # `re.search(r"\d+")` would take the first digit run, turning a
            # probability like 0.95 into confidence 0 — the worst attainable
            # Brier score, reported as a clean success.
            raise ValueError(f"Trailing token is not a confidence: {token!r}")
        confidence = int(token.rstrip("% ").strip())
    else:
        tokens = content.split()
        if len(tokens) < 2:
            raise ValueError(f"No separable confidence in answer: {content!r}")
        if not _CONFIDENCE_TOKEN.match(tokens[-1]):
            raise ValueError(f"Trailing token is not a confidence: {tokens[-1]!r}")
        answer = " ".join(tokens[:-1]).strip()
        confidence = int(tokens[-1].rstrip("% ").strip())
    if not answer:
        raise ValueError(f"Empty answer parsed from: {content!r}")
    return answer, confidence


def evaluate_predictions(output_file_path):
    _require("PQA")
    import numpy as np
    from sklearn.metrics import brier_score_loss

    with open(output_file_path, "r") as f:
        data = json.load(f)
    accs, cfds = [], []
    failed, total = 0, 0

    for item in data:
        total += 1
        try:
            generated_str = item["generated_response"]
            if "</think>" in generated_str:
                generated_str = generated_str.split("</think>")[-1]
            content = (
                re.search(
                    r"\[ANSWER_START\](.*?)\[ANSWER_END\]", generated_str, re.DOTALL
                )
                .group(1)
                .strip()
            )

            answer, confidence = _split_answer_and_confidence(content)
            expected = item["answer"]

            cfds.append(min(confidence, 100))
            accs.append(1 if answer == expected else 0)
        except Exception:
            failed += 1

    accuracy = sum(accs) / len(accs) if accs else 0
    brier = brier_score_loss(accs, np.array(cfds) / 100) if accs else None
    return {
        "Accuracy": accuracy,
        "Brier_Score": brier,
        "Failed_Rate": failed / total if total else 0,
    }


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
        "REA-ERR": evaluate_step_reasoning_model,
        "ERR": evaluate_correction_task,
        "GEN": evaluate_protocolgen_model,
        "ORD": evaluate_sorting_predictions,
        "PQA": evaluate_predictions,
    }

    if task_name not in eval_dispatch:
        return {
            "error": f"Unknown task: {task_name}. Supported tasks: {list(eval_dispatch.keys())}"
        }

    # Reject a file with no records up front. Every evaluator guards its
    # divisions with `if total else 0`, so an empty list would otherwise score
    # as all-zero metrics at Failed_Rate 0 — i.e. "status": "success" for a run
    # that measured nothing.
    try:
        with open(response_file_path, "r") as f:
            records = json.load(f)
    except Exception as e:
        return {
            "error": (
                f"Could not read {response_file_path} as JSON: "
                f"{type(e).__name__}: {e}"
            ),
            "task": task_name,
        }
    if not isinstance(records, list):
        return {
            "error": (
                f"{response_file_path} must be a JSON list of response records; "
                f"got {type(records).__name__}."
            ),
            "task": task_name,
        }
    if not records:
        return {
            "error": (
                f"{response_file_path} contains no response records, so there is "
                f"nothing to score for task {task_name}."
            ),
            "task": task_name,
        }

    try:
        metrics = eval_dispatch[task_name](response_file_path)
    except Exception as e:
        # A bare str(e) turns a KeyError into the uninterpretable message "'X'".
        # Name the task and the exception type, and carry the traceback.
        return {
            "error": (
                f"Evaluation failed during execution of task {task_name} on "
                f"{response_file_path}: {type(e).__name__}: {e}"
            ),
            "task": task_name,
            "traceback": traceback.format_exc(),
        }
    return {
        "task": task_name,
        "status": _result_status(metrics),
        "metrics": metrics,
    }


def _result_status(metrics: dict) -> str:
    """Describe how much of the run actually scored.

    'success' overstates a run in which every record failed to parse (the usual
    symptom of handing over a file with no ground truth merged in), and GEN can
    return all-None metrics that way.
    """
    if "Failed_Rate" in metrics:
        failed_rate = metrics["Failed_Rate"]
    elif "failed_rate" in metrics:
        failed_rate = metrics["failed_rate"]
    elif "Failure_Rate" in metrics:
        failed_rate = metrics["Failure_Rate"] / 100.0
    else:
        failed_rate = 0

    if failed_rate >= 1.0:
        return "failed"
    if failed_rate > 0 or metrics.get("Unjudged"):
        return "partial"
    return "success"


# ==========================================
# 5. Agent Inference & Script Bridging
# ==========================================
_MISSING = object()


@contextlib.contextmanager
def _temporary_modules(mapping: dict):
    """Bind `mapping` into sys.modules for the duration of the block only."""
    saved = {}
    try:
        for name, module in mapping.items():
            saved[name] = sys.modules.get(name, _MISSING)
            sys.modules[name] = module
        yield
    finally:
        for name, previous in saved.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _load_script(module_name: str, file_name: str, siblings=()):
    """Load a vendored Scripts/*.py privately.

    The scripts are loaded under the `_SCRIPT_NS` prefix and are never left in
    sys.modules: putting `Scripts/` on sys.path would publish the generic
    top-level names `prompt_format`, `generate_response` and
    `generate_response_local` into the shared persistent kernel, shadowing
    anything the agent later imports under those names, and would let the
    scripts be imported around the skill gate's sha256 pinning and load-event
    capture.

    `siblings` names the modules a script imports by bare name (e.g.
    `prompt_format`); each is loaded privately and bound into sys.modules only
    while the dependent script executes.
    """
    file_path = os.path.join(script_dir, file_name)
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Original script {file_name} not found in {script_dir}"
        )

    provided = {}
    for sibling_name, sibling_file in siblings:
        provided[sibling_name] = _load_script(
            f"{module_name}__{sibling_name}", sibling_file
        )

    qualified = f"{_SCRIPT_NS}.{module_name}"
    spec = importlib.util.spec_from_file_location(qualified, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create a module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    provided[qualified] = module
    with _temporary_modules(provided):
        spec.loader.exec_module(module)
    return module


_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_path_component(value: str, fallback: str) -> str:
    """Reduce caller-supplied text to a single safe filename component."""
    cleaned = _UNSAFE_PATH_CHARS.sub("_", str(value)).strip("._-")
    return cleaned or fallback


def get_task_prompt(sample: dict, task_name: str) -> str:
    """Agent interface to fetch the standardized prompt for a specific sample."""
    try:
        prompt_format = _load_script("prompt_format", "prompt_format.py")
        return prompt_format.generate_user_prompt(sample, task_name)
    except Exception as e:
        return f"Error loading prompt format: {e}"


def run_llm_judge_evaluation(
    sample: dict,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model_name: str = "deepseek-chat",
) -> str:
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


def trigger_batch_inference(
    task_name: str,
    test_file_path: str,
    mode: str = "API",
    model_name: str = "o3-mini",
    api_key: str = None,
    base_url: str = None,
) -> dict:
    """Agent interface to trigger full dataset batch inference.

    `base_url` selects the OpenAI-compatible endpoint the key is sent to. It
    must be passed whenever the key is not an OpenAI one: the vendored script's
    constant points at api.openai.com, so defaulting to it would transmit a
    deepseek/ark/self-hosted credential — and every prompt — to a vendor the
    caller never chose.
    """
    # `task_name` and `model_name` are caller-supplied and land in a filename;
    # a value like "../../etc/x" or "a/b" must not escape the working directory.
    safe_task = _safe_path_component(task_name, "task")
    safe_model = _safe_path_component(model_name, "model")
    try:
        if mode == "API":
            if not api_key:
                return {
                    "error": "API mode requires an api_key provided securely by the agent."
                }
            from openai import OpenAI

            api_inf = _load_script(
                "api_inf",
                "generate_response.py",
                siblings=(("prompt_format", "prompt_format.py"),),
            )

            # Dynamically override the script's global configs
            api_inf.API_KEY = api_key
            api_inf.MODEL_NAME = model_name
            api_inf.TASK_NAME = task_name
            api_inf.TEST_FILE_PATH = test_file_path
            api_inf.OUTPUT_FILE = f"{safe_task}_test_{safe_model}_api.json"
            endpoint = base_url or api_inf.BASE_URL
            api_inf.BASE_URL = endpoint
            api_inf.client = OpenAI(api_key=api_key, base_url=endpoint)

            api_inf.main()
            return {
                "status": "success",
                "message": f"API batch inference for {task_name} completed. Saved to {api_inf.OUTPUT_FILE}.",
            }

        elif mode == "Local":
            local_inf = _load_script(
                "local_inf",
                "generate_response_local.py",
                siblings=(("prompt_format", "prompt_format.py"),),
            )

            # Dynamically override the script's global configs
            local_inf.MODEL_NAME = model_name
            local_inf.TASK_NAME = task_name
            local_inf.TEST_FILE_PATH = test_file_path
            local_inf.OUTPUT_FILE = f"{safe_task}_test_{safe_model}_local.json"

            local_inf.main()
            return {
                "status": "success",
                "message": f"Local batch inference for {task_name} completed. Saved to {local_inf.OUTPUT_FILE}.",
            }
        else:
            return {"error": "Unknown mode. Choose 'API' or 'Local'."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
