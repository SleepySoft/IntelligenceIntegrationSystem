#!/bin/bash

# ================= 1. 硬件环境 =================
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0,1
export DISABLE_VERSION_CHECK=1

# ================= 2. 通信优化 (IOMMU 已关，全速模式) =================
# 纯 DDP 模式下，P2P 非常重要，确保它是开启的
# export NCCL_P2P_DISABLE=1   <-- 注释掉
# export NCCL_IB_DISABLE=1    <-- 你的环境可能没 IB，禁用它防止报错
export NCCL_ASYNC_ERROR_HANDLING=1

# ================= 3. 显存优化 =================
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
# 尝试让 MIOpen 自动寻找最快算法 (解决 Generic Kernel 慢的问题)
export MIOPEN_FIND_MODE=1 

# 强制开启 GPU 间的 PCIe P2P 通信 (解决 73秒 通信延迟的关键)
export HSA_ENABLE_SDMA=1
export HSA_FORCE_FINE_GRAIN_PCIE=1

# ================= 4. 路径 =================
MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft_ddp_native"

# ================= 5. 启动命令 (Native DDP) =================
# 注意：移除了 --deepspeed 参数
# 使用 torchrun 启动 (替代 deepspeed launcher)
torchrun --nproc_per_node=2 --master_port=29504 src/train.py \
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
    --per_device_train_batch_size 2 \
    --gradient_accumulation_steps 16 \
    --learning_rate 5e-5 \
    --num_train_epochs 5.0 \
    --lr_scheduler_type cosine \
    --warmup_ratio 0.1 \
    --fp16 True \
    --logging_steps 1 \
    --save_steps 500 \
    --plot_loss True \
    --cutoff_len 2048 \
    --preprocessing_num_workers 16 \
    --gradient_checkpointing True \
    --flash_attn disabled \
    --ddp_timeout 18000000 \
    --optim adamw_torch