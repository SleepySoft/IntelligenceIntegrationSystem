#!/bin/bash

export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0,1
export DISABLE_VERSION_CHECK=1
export NCCL_ASYNC_ERROR_HANDLING=1
export PL_TORCH_DISTRIBUTED_BACKEND=nccl
# 显存碎片优化 (FP32模式下这很重要)
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
export MIOPEN_FIND_MODE=1 

MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32"

# 启动命令
torchrun --nproc_per_node=2 --master_port=29507 src/train.py \
    --stage sft \
    --do_train \
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
    --per_device_train_batch_size 1 \
    --gradient_accumulation_steps 32 \
    --learning_rate 1e-5 \
    --num_train_epochs 5.0 \
    --lr_scheduler_type cosine \
    --warmup_ratio 0.1 \
    --fp16 False \
    --bf16 False \
    --logging_steps 1 \
    --save_steps 200 \
    --plot_loss True \
    --cutoff_len 1024 \
    --preprocessing_num_workers 16 \
    --gradient_checkpointing True \
    --flash_attn disabled \
    --ddp_timeout 18000000 \
    --optim adamw_torch
