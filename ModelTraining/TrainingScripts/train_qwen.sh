#!/bin/bash

# ================= 1. 基础硬件适配 =================
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0,1
export DISABLE_VERSION_CHECK=1

# ================= 2. 核弹级防死锁配置 (纯 TCP 模式) =================
# [关键] 禁用 P2P (卡间直连)
export NCCL_P2P_DISABLE=1
# [关键] 禁用 Shared Memory (共享内存)，强制走 Socket，解决本地死锁
export NCCL_SHM_DISABLE=1
# [关键] 禁用 InfiniBand
export NCCL_IB_DISABLE=1
# [关键] 强制只用本地回环网络
export NCCL_SOCKET_IFNAME=lo
# 启用阻塞报错，如果有问题直接抛出异常而不是卡死
export NCCL_ASYNC_ERROR_HANDLING=1
export PL_TORCH_DISTRIBUTED_BACKEND=nccl

# ================= 3. 离线模式 =================
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# ================= 4. 路径配置 =================
MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft_dual_gpu_safe"

# ================= 5. 启动命令 =================
# 显存优化：Batch=2 + GradCheck=True
deepspeed --num_gpus 2 --master_port=29502 src/train.py \
    --deepspeed ds_z2_config.json \
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
    --bf16 True \
    --logging_steps 1 \
    --save_steps 500 \
    --plot_loss True \
    --cutoff_len 2048 \
    --preprocessing_num_workers 16 \
    --gradient_checkpointing True \
    --ddp_timeout 18000000 \
    --optim adamw_torch