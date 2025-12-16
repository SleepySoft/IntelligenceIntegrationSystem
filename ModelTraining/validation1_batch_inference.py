import json
import torch
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

# ================= 配置 =================
BASE_MODEL_PATH = "/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
# 指向你觉得最好的那个 checkpoint
ADAPTER_PATH = "./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32/checkpoint-xxx"
TEST_DATA_PATH = "data/test.json"  # 你的测试集路径
OUTPUT_FILE = "eval_results.jsonl"


def main():
    # 1. 加载模型 (注意使用 FP16 或 FP32 取决于你的显卡支持，这里用 auto 适配)
    print("Loading model...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_PATH,
        device_map="auto",
        torch_dtype=torch.float16,  # 如果之前是FP32训练，推理用FP16通常也没问题，且更快
        trust_remote_code=True
    )
    # 加载 LoRA
    model = PeftModel.from_pretrained(model, ADAPTER_PATH)
    model.eval()

    # 2. 读取数据
    with open(TEST_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)  # 假设是 List[Dict] 格式

    results = []

    # 3. 批量推理
    print(f"Starting inference on {len(data)} samples...")
    for item in tqdm(data):
        instruction = item.get("instruction", "")
        input_text = item.get("input", "")
        ground_truth = item.get("output", "")

        # 构建 Prompt (需与训练时 Template 一致)
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": instruction + "\n" + input_text}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

        with torch.no_grad():
            generated_ids = model.generate(
                **model_inputs,
                max_new_tokens=512,
                temperature=0.7,  # 评估时稍微降低随机性
                top_p=0.9
            )
            generated_ids = [
                output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]
            response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

        # 4. (可选) 简单的自动化分数提取逻辑
        # 假设输出里包含 "Score: 5" 这样的格式，可以用正则提取并对比
        # auto_score = extract_score(response)
        # true_score = extract_score(ground_truth)

        results.append({
            "instruction": instruction,
            "input": input_text,
            "ground_truth": ground_truth,
            "model_output": response,
            "human_label": None,  # 留给人工填
            "comments": ""
        })

    # 4. 保存结果
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for entry in results:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Done! Results saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
