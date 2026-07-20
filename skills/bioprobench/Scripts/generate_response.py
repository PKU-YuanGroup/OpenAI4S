import os
import json
import time
from tqdm import tqdm
from openai import OpenAI
from prompt_format import generate_user_prompt

# ================================
# User Configuration
# ================================
        
API_KEY = 'YOUR API-KEY'       # Replace this with your API key
BASE_URL = 'https://api.openai.com/v1'  # Replace with your base URL if different
MODEL_NAME = 'o3-mini'              # Replace with your preferred model
TASK_NAME = 'PQA'                   # Task name used in file paths ('PQA', 'ORD', 'ERR', 'REA-ERR', 'GEN', 'REA-GEN')
TEST_FILE_PATH = f"../Data/{TASK_NAME.split('-')[-1]}_test.json"
OUTPUT_FILE = f'./{TASK_NAME}_test_{MODEL_NAME}.json'

print(f"Using model: {MODEL_NAME} for task: {TASK_NAME}......")

# ================================
# Initialize OpenAI Client
# ================================

client = OpenAI(api_key=API_KEY,base_url=BASE_URL)

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
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
                stream=False,
                max_tokens=8192
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            last_exception = e
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                time.sleep(delay)

    raise Exception(f"All {max_retries} attempts failed") from last_exception


def process_sample(sample, model_name, task_name):
    """
    Process a single sample by generating a response using the model.
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
