#We use LLM (deepseek-chat here) as a judge to evaluate the consitency of the model-generated response with the error description in REA-ERR task. For more details, please refer to our paper.
import os
import json
import time
from tqdm import tqdm
from openai import OpenAI

# ================================
# User Configuration
# ================================

API_KEY = 'YOUR_API_KEY'   
BASE_URL = 'https://api.deepseek.com'  
MODEL_NAME = 'deepseek-chat'  

TEST_FILE_PATH = './REA-ERR_test_o3-mini.json'  # For example, we use LLM judge to evaluate the consistency of o3-mini's responses
OUTPUT_FILE = TEST_FILE_PATH

print(f"Use LLM-as-a-judge to evaluate the consistency of model-generated responses with error descriptions in {TEST_FILE_PATH}")

# ================================
# Initialize OpenAI Client
# ================================

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# ================================
# Functions
# ================================

def get_test_data(file_path):
    """
    Load test data from a JSON file.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        test_data = json.load(f)
    return test_data

def generate_response(user_prompt, model_name, max_retries=5, initial_delay=1):
    """
    Call the OpenAI API to generate a response for the given user prompt.
    Includes a retry mechanism with exponential backoff.
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=8192,
                stream=False
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            last_exception = e
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                time.sleep(delay)

    raise Exception(f"All {max_retries} attempts failed") from last_exception

def generate_user_prompt(sample):
    """
    Format the user prompt for LLM evaluation.
    """
    corrupted_text = sample.get('corrupted_text', '')
    corrected_text = sample.get('corrected_text', '')
    error_description = sample.get('error_description', 'No error description provided.')
    generated_response = sample.get('generated_response', '').split('</think>')[-1].split('[/INST]')[-1]

    prompt = f"""You are given a task to judge whether a model-generated response correctly identifies the key error in a scientific protocol step. You will receive the following inputs:

- `corrupted_text`: the original text containing the error.
- `corrected_text`: the corrected version of the text.
- `error_description`: a brief explanation of the specific error in the corrupted text.
- `generated_response`: the model's analysis and judgment of the corrupted text.

Your task is to read the `error_description`, compare the `corrupted_text` and `corrected_text` to understand the specific correction made, and determine whether the `generated_response` successfully and explicitly identifies the same issue described in `error_description`.

Focus **only** on whether the `generated_response` identifies the **same problem** as described in the `error_description`, even if it finds other unrelated issues.

At the end of your judgment, output only one of the following two values (without explanation):

**True** – if the generated response accurately identifies the error described.

**False** – if the generated response misses or incorrectly identifies the error described.

---

Input:
corrupted_text: {corrupted_text}
corrected_text: {corrected_text}
error_description: {error_description}
generated_response: {generated_response}

---

Output your final answer in the following format:
[ANSWER_START]True/False[ANSWER_END]

Now give me your final answer:"""
    return prompt

def process_sample(sample, model_name):
    """
    Process a single sample by generating a response using the model.
    Skips processing if the sample already has a generated LLM judge response.
    """
    if 'LLM_judge' in sample:
        return sample
    if sample.get('corrupted_text'):
        user_prompt = generate_user_prompt(sample)
        response = generate_response(user_prompt, model_name)
        sample['LLM_judge'] = response
    return sample

def save_checkpoint(data, filename):
    """
    Save intermediate results to a JSON file.
    """
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ================================
# Main Processing Function
# ================================

def main():
    """
    Main function to process the dataset.
    Loads test data, processes each sample, and saves results periodically.
    """
    test_set = get_test_data(TEST_FILE_PATH)

    # Load checkpoint if exists
    processed_set = []
    if os.path.exists(OUTPUT_FILE):
        print("Loading from checkpoint")
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            processed_set = json.load(f)

    # Identify already processed samples
    processed_ids = set(sample['id'] for sample in processed_set if 'LLM_judge' in sample)
    remaining_samples = [sample for sample in test_set if sample['id'] not in processed_ids]

    count_since_last_save = 0
    for sample in tqdm(remaining_samples, desc="Processing samples"):
        processed_sample = process_sample(sample, MODEL_NAME)
        processed_set.append(processed_sample)
        count_since_last_save += 1

        if count_since_last_save >= 10:
            save_checkpoint(processed_set, OUTPUT_FILE)
            print(f"Checkpoint saved after processing {len(processed_set)} samples.")
            count_since_last_save = 0

    save_checkpoint(processed_set, OUTPUT_FILE)
    print(f"All data saved to {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
