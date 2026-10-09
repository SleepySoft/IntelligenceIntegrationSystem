import json
import torch
import os
import argparse
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


"""
# 指定使用 GPU 0
export HIP_VISIBLE_DEVICES=0

python validation1_batch_inference.py \
    --adapter ./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32/checkpoint-100 \
    --data data/alpaca_test.json \
    --output result_ckpt100.jsonl

# 指定使用 GPU 1
export HIP_VISIBLE_DEVICES=1

python validation1_batch_inference.py \
    --adapter ./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32/checkpoint-150 \
    --data data/alpaca_test.json \
    --output result_ckpt150.jsonl
"""


# ================= 1. 针对 MI50 的环境配置 =================
os.environ["HSA_OVERRIDE_GFX_VERSION"] = "9.0.6"
os.environ["PYTORCH_HIP_ALLOC_CONF"] = "expandable_segments:True"


def main(adapter_path, test_data_path, output_file):
    # 基础模型路径
    BASE_MODEL_PATH = "/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"

    print(f"🔄 Processing Adapter: {adapter_path}")
    print(f"📂 Input Data: {test_data_path}")
    print(f"💾 Output File: {output_file}")

    # ================= 2. 加载数据与断点续传检测 =================
    with open(test_data_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)

    start_index = 0
    # 检测输出文件是否存在，如果存在则计算已跑行数
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f_out:
            # 计算非空行数
            lines = [line for line in f_out if line.strip()]
            start_index = len(lines)
            if start_index > 0:
                print(f"⚠️  Found existing file with {start_index} samples. Resuming from index {start_index}...")

    # 截取剩余需要跑的数据
    data_to_process = all_data[start_index:]

    if len(data_to_process) == 0:
        print("🎉 All data processed! Nothing to do.")
        return

    # ================= 3. 加载模型 (强制 FP32) =================
    print("⏳ Loading model into VRAM (FP32 Mode)...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_PATH,
        device_map="auto",
        torch_dtype=torch.float32,
        trust_remote_code=True
    )

    print(f"🔗 Merging LoRA weights from {adapter_path}...")
    model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    # ================= 4. 流式推理与写入 =================
    print(f"🚀 Starting inference on remaining {len(data_to_process)} samples...")

    # 自动创建目录
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)

    # 使用 'a' (append) 模式打开文件
    # buffering=1 表示行缓冲，flush() 会更有效
    with open(output_file, 'a', encoding='utf-8', buffering=1) as f:

        for item in tqdm(data_to_process, desc="Inference"):
            instruction = item.get("instruction", "")
            input_text = item.get("input", "")
            ground_truth = item.get("output", "")

            # 构造 Prompt
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": instruction + "\n" + input_text}
            ]

            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

            try:
                with torch.no_grad():
                    generated_ids = model.generate(
                        **model_inputs,
                        max_new_tokens=512,
                        temperature=0.7,
                        top_p=0.9
                    )

                generated_ids = [
                    output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
                ]
                response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            except Exception as e:
                print(f"\n❌ Error generating sample: {e}")
                response = "ERROR_GENERATION"

            result_entry = {
                "instruction": instruction,
                "input": input_text,
                "ground_truth": ground_truth,
                "model_output": response,
                "adapter": adapter_path
            }

            # 【核心修改】立即写入并刷新
            # ensure_ascii=False 保证写入的是中文而不是 \uXXXX
            f.write(json.dumps(result_entry, ensure_ascii=False) + "\n")

            # 强制将缓冲区内容刷入硬盘，此时别人打开文件就能看到最新的一行
            f.flush()
            # os.fsync(f.fileno()) # 如果你是极端掉电恐惧症，可以取消这行注释，但会稍微慢一点点

    print(f"✅ Done! Results saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=str, required=True, help="Path to checkpoint folder")
    parser.add_argument("--data", type=str, default="alpaca_test.json", help="Path to test json")
    parser.add_argument("--output", type=str, required=True, help="Path to output jsonl")

    args = parser.parse_args()
    main(args.adapter, args.data, args.output)
