#!/bin/bash

# ================= 1. 基础硬件适配 =================
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0,1
export DISABLE_VERSION_CHECK=1

# ================= 2. 全速通信配置 (IOMMU 已关，开启 P2P) =================
# [关键修改] 允许 P2P 直连 (IOMMU关闭后，这是安全的且最快的)
# export NCCL_P2P_DISABLE=1  <-- 注释掉它！

# [关键修改] 允许共享内存 (本地双卡通信的极速通道)
# export NCCL_SHM_DISABLE=1  <-- 注释掉它！

# [关键修改] 解除网络绑定，自动选择最佳路径
# export NCCL_SOCKET_IFNAME=lo <-- 注释掉它！

# 禁用 InfiniBand (你没有 IB 硬件，这个保持禁用以防报错)
export NCCL_IB_DISABLE=1

# 保持阻塞报错，方便调试
export NCCL_ASYNC_ERROR_HANDLING=1
export PL_TORCH_DISTRIBUTED_BACKEND=nccl

# ================= 3. 离线模式 & 显存优化 =================
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
# 显存防碎片化 (保留，有益无害)
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True

# 强制开启 GPU 间的 PCIe P2P 通信 (解决 73秒 通信延迟的关键)
export HSA_ENABLE_SDMA=1
export HSA_FORCE_FINE_GRAIN_PCIE=1


# ================= 4. 路径配置 =================
MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft_dual_gpu_fast"

# ================= 5. 启动命令 =================
# 保持 Batch=2 和 梯度累积=16
deepspeed --num_gpus 2 --master_port=29503 src/train.py \
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