#!/bin/bash
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0
export DISABLE_VERSION_CHECK=1

# 模型路径
MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"

python src/train.py \
    --stage sft \
    --do_train \
    --model_name_or_path "$MODEL_PATH" \
    --dataset "intelligence_alpaca" \
    --template qwen \
    --finetuning_type lora \
    --lora_target all \
    --lora_rank 16 \
    --lora_alpha 32 \
    --output_dir "./saves/test_throughput" \
    --overwrite_output_dir \
    --per_device_train_batch_size 8 \
    --gradient_accumulation_steps 1 \
    --learning_rate 1e-5 \
    --num_train_epochs 1.0 \
    --fp16 True \
    --logging_steps 1 \
    --cutoff_len 1024 \
    --preprocessing_num_workers 4 \
    --flash_attn disabled \
    --gradient_checkpointing False \
    --optim adamw_torch