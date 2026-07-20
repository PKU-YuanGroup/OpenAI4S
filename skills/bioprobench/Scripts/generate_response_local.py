import os
import json
from tqdm import tqdm
from prompt_format import generate_user_prompt
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline

# ================================
# User Configuration
# ================================

MODEL_NAME = 'meta-llama/Meta-Llama-3-8B-Instruct'                 # or other models from huggingface or local path
TASK_NAME = 'PQA'                   # Task name used in file paths ('PQA', 'ORD', 'ERR', 'REA-ERR', 'GEN', 'REA-GEN')
TEST_FILE_PATH = f"../Data/{TASK_NAME.split('-')[-1]}_test.json"
OUTPUT_FILE = f'./{TASK_NAME}_test_{MODEL_NAME}.json'

print(f"Using local model: {MODEL_NAME} for task: {TASK_NAME}......")

# ================================
# Initialize Local Model
# ================================

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
generator = pipeline("text-generation", model=model, tokenizer=tokenizer, device=0)  # use GPU:0 for inference

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

def generate_response(user_prompt, model_name, max_length=512):
    """
    Generate a response using the local model.
    """
    outputs = generator(
        user_prompt,
        max_length=max_length,
        num_return_sequences=1,
        do_sample=True,
        top_k=50,
        top_p=0.95,
        temperature=0.7
    )
    response_text = outputs[0]['generated_text'][len(user_prompt):].strip()  # Remove the prompt from the output
    return response_text

def process_sample(sample, model_name, task_name):
    """
    Process a single sample by generating a response using the local model.
    Skips processing if the sample already has a generated response.
    """
    if 'generated_response' in sample:
        return sample

    user_prompt = generate_user_prompt(sample, task_name)
    response = generate_response(user_prompt, model_name)
    sample['generated_response'] = response
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
    Loads test data, processes each sample sequentially, and saves results periodically.
    """
    test_set = get_test_data(TEST_FILE_PATH)

    # Load existing checkpoint if available
    processed_set = []
    if os.path.exists(OUTPUT_FILE):
        print("Loading from checkpoint")
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            processed_set = json.load(f)

    # Identify already processed samples
    processed_ids = set(sample['id'] for sample in processed_set if 'generated_response' in sample)
    remaining_samples = [sample for sample in test_set if sample['id'] not in processed_ids]

    count_since_last_save = 0
    for sample in tqdm(remaining_samples, desc="Processing samples"):
        processed_sample = process_sample(sample, MODEL_NAME, TASK_NAME)
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
