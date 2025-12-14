#!/bin/bash

# ================= 关键环境变量 (MI50 必须) =================
# 1. 伪装显卡架构，防止 ROCm 识别错误
export HSA_OVERRIDE_GFX_VERSION=9.0.6
# 2. 指定 ROCm 路径
export ROCM_HOME=/opt/rocm
# 3. 显卡可见性
export HIP_VISIBLE_DEVICES=0,1
# 4. 强制离线模式，防止尝试连 HuggingFace 报错
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# ================= 路径配置 =================
# 模型路径 (请修改为你第二步下载的实际绝对路径)
# 例如: /home/sleepy/Public/ModelTrain/LLaMA-Factory/qwen/Qwen2.5-7B-Instruct
MODEL_PATH="/home/sleepy/Public/ModelTrain/LLaMA-Factory/qwen/Qwen2.5-7B-Instruct"

# 输出路径
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft"

# ================= 启动命令 =================
deepspeed --num_gpus 2 src/train.py \
    --deepspeed ds_z2_config.json \
    --stage sft \
    --do_train \
    --use_param_tokenizer_template False \
    --model_name_or_path "$MODEL_PATH" \
    --dataset "intelligence_alpaca" \
    --template qwen \
    --finetuning_type lora \
    --lora_target all \
    --lora_rank 64 \
    --lora_alpha 128 \
    --lora_dropout 0.05 \
    --output_dir "$OUTPUT_PATH" \
    --overwrite_output_dir \
    --per_device_train_batch_size 4 \
    --gradient_accumulation_steps 8 \
    --learning_rate 5e-5 \
    --num_train_epochs 5.0 \
    --lr_scheduler_type cosine \
    --warmup_ratio 0.1 \
    --bf16 True \
    --logging_steps 10 \
    --save_steps 500 \
    --plot_loss True \
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
