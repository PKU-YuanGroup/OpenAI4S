def generate_user_prompt(sample, task_name):
    """
    Generate a prompt for the given sample and task.
    """
    if task_name == 'PQA':
        prompt = f"""
You will be given a multiple-choice question related to a biological protocol. The blank in the question (represented as '____') indicates where the correct choice should be filled in.

Question:
{sample['question']}

Choices:
{sample['choices']}

Your task:
- Choose the most likely correct answer from the given choices.
- You must always select *one* answer, even if you are unsure.
- The selected answer must match one of the choices exactly (including case and punctuation).
- Assign a confidence score between 0 and 100 based on your certainty.
- Output your answer *wrapped exactly* between the tags [ANSWER_START] and [ANSWER_END].
- The format of your response must be:
[ANSWER_START]your selected choice & your confidence score[ANSWER_END]
"""
    elif task_name == 'ORD':
        prompt = f"""
{sample['question']}
The steps are:
{sample['wrong_steps']}

- Give me the correct order of the steps as a list of their original indices (start from 0), no other words.
- Output your answer *wrapped exactly* between the tags [ANSWER_START] and [ANSWER_END].
- The format of your response must be:
[ANSWER_START]a list of the original indices[ANSWER_END]
"""
    elif task_name == 'ERR':
        if sample['is_correct']:
            step=sample['corrected_text']
        else:
            step=sample['corrupted_text']
        prompt = f"""Determine whether the following target step in a protocol is True or False:
{step}

You may use the following context, which includes the purpose of the step, as well as the preceding and following steps, to inform your decision:
{sample['context']}

Please carefully evaluate if the step is logically consistent, necessary, and accurate in the context. If you find anything wrong, answer False.

- Please respond with only True or False, without any additional explanation.
- Output your answer *wrapped exactly* between the tags [ANSWER_START] and [ANSWER_END].
- The format of your response must be:
[ANSWER_START]True or False[ANSWER_END]
"""
    elif task_name == 'REA-ERR':
        if sample['is_correct']:
            step=sample['corrected_text']
        else:
            step=sample['corrupted_text']
        prompt = f"""Evaluate the validity of the following target step in a protocol. Follow the detailed reasoning process demonstrated in the example below to identify potential errors across Operation, Reagent, and Parameter categories, with meticulous attention to numerical values and their consistency with the provided context and typical practices.

---

Example Start

**Example Target Step:**
Mix 860µL of sterile deionized water and 14µL of 5% sodium hypochlorite in a 1.5mL tube.

**Example Context:**
{{
    "purpose": "Sterilization of seeds to remove surface contaminants using sodium hypochlorite.",
    "prior_step": "1.1 Place transgenic Arabidopsis seeds in a 1.5mL tube.",
    "next_step": "1.2.2 Vigorously mix the contents of the tube using a vortex mixer."
}}

**Example Reasoning Process:**

1.  **Operation Error:** The operations (Mix) and the use of a 1.5mL tube are standard. No obvious operational errors.
2.  **Reagent Error:** The reagents are appropriate. However, the specified volume of 5% sodium hypochlorite is 14µL, mixed with 860µL water. This results in a very dilute solution (~0.07%). For sterilization, typical practice suggests a final concentration of around 0.5–1% sodium hypochlorite. Therefore, the reagent volume is significantly too low, which undermines effectiveness and contradicts the stated sterilization purpose.
3.  **Parameter Error:** Although explicit parameters like time and temperature are not mentioned, the concentration of sodium hypochlorite functions as a critical parameter in disinfection efficacy. Here, the final concentration (~0.07%) is too low to be effective, making it a parameter error as well.

Based on the significant numerical error in both Reagent volume and the effective concentration (parameter), the step is invalid.

**Example Answer:**
[ANSWER_START]False[ANSWER_END]

---
Example End

Now, evaluate the following target step using the same detailed reasoning process demonstrated in the example above:

Evaluate the validity of the target step:
{step}

You may use the following context, which includes the purpose of the target step, as well as the preceding and following steps, to inform your decision:
{sample['context']}

Analyze the step, paying meticulous attention to all numerical values (e.g., times, temperatures, volumes, concentrations, speeds, durations), by reasoning through the following three categories of potential errors. As part of this analysis, explicitly compare numerical values specified in the target step and consider typical laboratory practices.

Only evaluate the correctness of the information explicitly present in the target step. Do not make assumptions about missing details. Focus solely on identifying errors in what is actually stated.

The format of your final answer must be:
[ANSWER_START]True or False[ANSWER_END]
"""
    elif task_name == 'GEN':
        prompt = f"""{sample['system_prompt']}
{sample['instruction']}
Format requirements:
- Each step must be on a separate line.

{sample['input']}"""
    elif task_name == 'REA-GEN':
        prompt = f"""{sample['system_prompt']}
{sample['instruction']}

Your response must be structured strictly for machine processing. It must contain two main parts in order:
1.  Your Chain of Thought (CoT) process, formatted with specific XML-like tags.
2.  The final detailed protocol steps, wrapped in [ANSWER_START][ANSWER_END] tags.

Please begin your response by outputting your thinking process. Follow this *exact* structure and include your analysis within the respective tags:

Let's think step by step:
<Objective>[Output the core objective of this protocol here]</Objective>.
To achieve this, <Precondition>[Output the necessary preconditions, materials, equipment, etc., here]</Precondition>.
The protocol must proceed as <Phase>[Output the logical division into key phases or stages here]</Phase>,
where critical parameters are <Parameter>[Output the critical parameters for each step/phase and the logic behind them here]</Parameter>.
Finally, <Structure>[Acknowledge and state the required output structure for the final steps here]</Structure>.

After outputting the complete thinking process exactly as structured above, output the final detailed protocol steps.

Format requirements for the final output steps (which must be placed *between* the [ANSWER_START] and [ANSWER_END] tags):
- Each step must be on a separate line.

[ANSWER_START]
[Output the detailed protocol steps here, ensuring each step is on a new line]
[ANSWER_END]

{sample['input']}"""
    else:
        raise ValueError(f"Unsupported task name: {task_name}")
    
    return prompt
