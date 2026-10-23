import json
import re
import time
import random
import torch
import os
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

# ================= 配置区域 =================
# 1. 本地模型配置
BASE_MODEL_PATH = "/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
# 替换为你想要评估的那个 Checkpoint 路径
ADAPTER_PATH = "./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32/checkpoint-xxx"

# 2. 数据集配置
TEST_DATA_PATH = "alpaca_test.json"  # 或者是 alpaca_val.json
OUTPUT_FILE = "evaluation_report.jsonl"

# 3. 评测配置
USE_REAL_MODEL = True  # True: 跑真实模型推理; False: 仅测试评分逻辑(用假数据)
USE_REAL_API = False  # True: 调用真实API; False: 使用Stub返回随机分
NUM_WORKERS = 4  # API 并发线程数


# ================= Part 1: API 客户端封装 =================

class APIClient:
    def __init__(self, use_stub: bool = True):
        self.use_stub = use_stub

    def chat(self,
             messages: List[Dict[str, str]],
             model: Optional[str] = "gpt-4o",  # 假设裁判是 GPT-4o 或类似强模型
             temperature: float = 0.1,  # 评测时温度要低，保证稳定性
             max_tokens: int = 4096,
             is_health_check: bool = False) -> Dict[str, Any]:
        """
        你的 API 接口实现
        """
        if self.use_stub:
            return self._stub_response(messages)

        # TODO: 这里填入你真实的 API 调用逻辑 (requests / sdk)
        # 模拟网络延迟
        time.sleep(0.5)
        # 这是一个 Mock 的返回结构，需根据你实际 API 返回修改
        return {
            "choices": [{
                "message": {
                    "content": self._mock_judge_logic()
                }
            }]
        }

    def _stub_response(self, messages) -> Dict[str, Any]:
        """测试用的桩"""
        time.sleep(0.1)
        return {
            "choices": [{
                "message": {
                    "content": self._mock_judge_logic()
                }
            }]
        }

    def _mock_judge_logic(self):
        """生成一个假的 JSON 评分返回"""
        score = random.randint(1, 10)
        reasoning = f"This is a stub evaluation. The model output length is fine. Random score: {score}."
        # 模拟 LLM 有时会带 Markdown 代码块，有时直接返回 JSON
        json_str = json.dumps({"score": score, "reasoning": reasoning})
        return f"```json\n{json_str}\n```"


# ================= Part 2: 评测 Prompt 模板 =================

JUDGE_PROMPT_TEMPLATE = """
### Task
You are an impartial and objective judge. You will be given an Instruction, an Input (optional), a Reference Answer (Ground Truth), and a Model Output.
Your task is to evaluate the quality of the 'Model Output' by comparing it to the 'Reference Answer' and the 'Instruction'.

### Scoring Criteria (1-10)
- **Accuracy**: Does the model answer the question correctly?
- **Completeness**: Does it cover all parts of the instruction?
- **Format**: Is the format correct (e.g., list, code, text)?
- **Hallucination**: Does the model invent false information?

### Input Data
**Instruction**: {instruction}
**Input**: {input}
**Reference Answer**: {ground_truth}
**Model Output**: {model_output}

### Output Format
You must return a strict JSON object with two fields:
1. "score": An integer from 1 to 10.
2. "reasoning": A concise explanation for the score.

Example output:
{{
    "score": 8,
    "reasoning": "The model answered correctly but missed one minor detail mentioned in the reference."
}}
"""


# ================= Part 3: 本地推理引擎 =================

class InferenceEngine:
    def __init__(self, base_path, adapter_path):
        print(f"Loading local model from {adapter_path}...")
        self.tokenizer = AutoTokenizer.from_pretrained(base_path, trust_remote_code=True)
        # 推理时可以用 FP16，显存占用小且快
        self.model = AutoModelForCausalLM.from_pretrained(
            base_path,
            device_map="auto",
            torch_dtype=torch.float16,
            trust_remote_code=True
        )
        self.model = PeftModel.from_pretrained(self.model, adapter_path)
        self.model.eval()
        print("Model loaded successfully.")

    def generate(self, instruction, input_text):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": f"{instruction}\n{input_text}"}
        ]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=512,
                temperature=0.7,
                top_p=0.9
            )
            # 裁剪掉 Input 部分，只留 Output
            generated_ids = [
                output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]
            response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response


# ================= Part 4: 主流程 (生成 + 自动评分) =================

def parse_judge_response(response_content: str) -> Dict:
    """解析 API 返回的 JSON，处理可能的 Markdown 格式"""
    try:
        # 移除 ```json 和 ``` 标记
        content = re.sub(r'```json\s*', '', response_content)
        content = re.sub(r'```\s*', '', content)
        content = content.strip()
        return json.loads(content)
    except Exception as e:
        return {"score": -1, "reasoning": f"Parse Error: {str(e)} | Raw: {response_content}"}


def run_evaluation():
    # 1. 初始化
    client = APIClient(use_stub=not USE_REAL_API)

    if USE_REAL_MODEL:
        engine = InferenceEngine(BASE_MODEL_PATH, ADAPTER_PATH)

    # 2. 读取测试集
    if not os.path.exists(TEST_DATA_PATH):
        print(f"Test data not found: {TEST_DATA_PATH}, creating dummy data.")
        # 创建假数据用于测试脚本流程
        test_data = [
                        {"instruction": "Calculate 1+1", "input": "", "output": "The answer is 2."}
                    ] * 5
    else:
        with open(TEST_DATA_PATH, 'r', encoding='utf-8') as f:
            test_data = json.load(f)
            # 为了测试方便，先只取前 10 条，实际跑时去掉切片
            # test_data = test_data[:10]

    results = []

    print(f"🚀 Starting Auto-Evaluation on {len(test_data)} samples...")
    print(f"Configuration: Real_Model={USE_REAL_MODEL}, Real_API={USE_REAL_API}, Workers={NUM_WORKERS}")

    # 3. 步骤一：本地推理 (Serial, GPU bound)
    # 如果已经有推理结果文件，可以跳过这一步直接读取
    inference_results = []
    for item in tqdm(test_data, desc="Local Inference"):
        if USE_REAL_MODEL:
            model_output = engine.generate(item.get("instruction", ""), item.get("input", ""))
        else:
            model_output = "Dummy model output for testing."

        item['model_output'] = model_output
        inference_results.append(item)

    # 4. 步骤二：API 评分 (Parallel, IO bound)
    print("⚖️  Submitting to Judge API...")

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_to_item = {}
        for item in inference_results:
            # 构造 Prompt
            prompt = JUDGE_PROMPT_TEMPLATE.format(
                instruction=item.get("instruction", ""),
                input=item.get("input", ""),
                ground_truth=item.get("output", ""),
                model_output=item.get("model_output", "")
            )

            messages = [{"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}]

            # 提交任务
            future = executor.submit(client.chat, messages=messages)
            future_to_item[future] = item

        # 获取结果
        completed_count = 0
        total_score = 0
        valid_scores = 0

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f_out:
            for future in tqdm(as_completed(future_to_item), total=len(inference_results), desc="Judging"):
                item = future_to_item[future]
                try:
                    # 获取 API 原始返回
                    api_resp = future.result()
                    # 假设你的 API 返回结构是标准的 OpenAI 格式
                    content = api_resp['choices'][0]['message']['content']

                    # 解析 JSON
                    judge_result = parse_judge_response(content)

                    item['judge_score'] = judge_result.get('score', -1)
                    item['judge_reasoning'] = judge_result.get('reasoning', 'No reasoning')

                    # 统计
                    if item['judge_score'] != -1:
                        total_score += item['judge_score']
                        valid_scores += 1

                    # 写入文件
                    f_out.write(json.dumps(item, ensure_ascii=False) + "\n")
                    f_out.flush()

                except Exception as e:
                    print(f"Error processing item: {e}")

    # 5. 总结报告
    avg_score = total_score / valid_scores if valid_scores > 0 else 0
    print("\n" + "=" * 30)
    print(f"📊 Evaluation Complete!")
    print(f"Output saved to: {OUTPUT_FILE}")
    print(f"Total Samples: {len(inference_results)}")
    print(f"Valid Evaluations: {valid_scores}")
    print(f"🏆 Average Score: {avg_score:.2f} / 10.0")
    print("=" * 30)


if __name__ == "__main__":
    run_evaluation()