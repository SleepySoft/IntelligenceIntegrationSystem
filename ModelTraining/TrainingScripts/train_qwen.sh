#!/bin/bash

# ================= 环境变量设置 =================
# 指定使用哪几张卡，ROCm 环境下通常用 0,1
export HIP_VISIBLE_DEVICES=0,1

# 强制使用离线模式，防止连接 HuggingFace 超时（前提是你已经下载好了模型）
# export HF_DATASETS_OFFLINE=1
# export TRANSFORMERS_OFFLINE=1

# ================= 核心路径配置 =================
# 1. 模型路径：请修改为你本地 Qwen2.5-7B-Instruct 的实际路径
MODEL_PATH="/path/to/your/Qwen2.5-7B-Instruct"

# 2. 输出路径：训练好的 LoRA 权重保存位置
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft"

# 3. 数据集名称：对应 dataset_info.json 里的 key
DATASET_NAME="intelligence_alpaca"

# ================= 启动命令 =================
# 使用 deepspeed 启动，自动检测双卡
deepspeed --num_gpus 2 src/train.py \
    --deepspeed ds_z2_config.json \
    --stage sft \
    --do_train \
    --use_param_tokenizer_template False \
    \
    --model_name_or_path "$MODEL_PATH" \
    --dataset "$DATASET_NAME" \
    --template qwen \
    --finetuning_type lora \
    \
    --lora_target all \
    --lora_rank 64 \
    --lora_alpha 128 \
    --lora_dropout 0.05 \
    \
    --output_dir "$OUTPUT_PATH" \
    --overwrite_output_dir \
    \
    --per_device_train_batch_size 4 \
    --gradient_accumulation_steps 8 \
    --learning_rate 5e-5 \
    --num_train_epochs 5.0 \
    --lr_scheduler_type cosine \
    --warmup_ratio 0.1 \
    \
    --bf16 True \
    --logging_steps 10 \
    --save_steps 500 \
    --plot_loss True \
    \
    --cutoff_len 4096 \
    --preprocessing_num_workers 16 \
    --ddp_timeout 18000000

# 注释说明：
# 1. --template qwen: 必须正确，否则对话模板会乱，导致模型学不会。
# 2. --lora_target all: 这是一个快捷方式，自动微调 q_proj, v_proj 等所有线性层，效果最好。
# 3. --per_device_train_batch_size 4: 单卡Batch 4，双卡就是 8。
# 4. --gradient_accumulation_steps 8: 梯度累积 8 步。
#    实际总 Batch Size = 4 * 2(GPUs) * 8 = 64。这是一个非常稳健的数值。
# 5. --cutoff_len 4096: 如果显存爆了 (OOM)，把这个数值降低到 2048。
