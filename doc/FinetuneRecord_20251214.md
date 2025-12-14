# Qwen2.5-7B 微调记录：AMD MI50 双卡踩坑与优化全复盘

**文档版本**: 1.0
**最后更新**: 2025-12-14
**硬件环境**: 
- CPU: Intel Xeon E5-2696 v2 (双路)
- GPU: 2x AMD Radeon Instinct MI50 (32GB VRAM, PCIe 3.0, Vega20 架构)
- OS: Ubuntu 22.04 / ROCm 6.x
- 框架: LLaMA-Factory, DeepSpeed, PyTorch

---

## 1. 项目背景
目标是在 AMD MI50 双卡环境下，使用 LLaMA-Factory 框架对 Qwen2.5-7B-Instruct 模型进行 LoRA 微调。由于 MI50 属于较老的 Vega20 架构（非 CDNA），且宿主机为 Intel Xeon 平台，过程中遇到了环境配置、通信死锁、算子兼容性及数值稳定性等多重挑战。

---

## 2. 调试历程与性能演进

### 阶段一：环境构建与依赖地狱

**问题 1**: `conda create` 报错 Terms of Service (ToS) 未签署。
- **调整**: 切换使用 `-c conda-forge` 社区源绕过官方源限制。

**问题 2**: PyTorch 官方未发布 ROCm 6.4 版本，只有 6.1/6.2。
- **调整**: 确认 ROCm 具有向下兼容性，在 ROCm 6.4 驱动环境下安装 `rocm6.1` 版 PyTorch。

**问题 3**: DeepSpeed 安装失败，报错 `ModuleNotFoundError: No module named 'dskernels'`。
- **分析**: 默认编译尝试构建推理优化的 CUTLASS 算子，但环境缺少相关库。
- **调整**: 仅编译训练所需算子，显式禁用推理模块。
  ```bash
  export DS_BUILD_INFERENCE=0
  export DS_BUILD_INFERENCE_HALIDE=0
  export DS_BUILD_INFERENCE_CUTLASS=0
  export DS_BUILD_CPU_ADAM=1
  export DS_BUILD_FUSED_ADAM=1
  export DS_BUILD_TRANSFORMER=1
  ```

**问题 4**: DeepSpeed 版本过高 (0.18.3) 导致 LLaMA-Factory 拒绝运行 (要求 <0.16.9)。

- **调整**: 强制跳过版本检查 export DISABLE_VERSION_CHECK=1。

### 阶段二：解决多卡通信死锁 (Deadlock)

#### 现象: 训练启动后卡在 ProcessGroupNCCL ... using GPU 0 to perform barrier，无任何进度，GPU 利用率为 0%。

**尝试 1 (软件层规避):**

- **调整**: 强制使用 lo 回环网络，禁用 P2P。

- **结果**: 依然卡死或极慢。

**尝试 2 (单卡验证):**

- **调整**: 切换单卡 (HIP_VISIBLE_DEVICES=0)，Batch Size=1。

- **结果**: 频繁 OOM (爆显存)。即使开启 Gradient Checkpointing 且 Cutoff_len=4096 也无法运行。

**优化:**

- **cutoff_len 降至 2048。**

- **gradient_accumulation_steps 设为 64 (Batch=1)。**

- **添加 export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True 缓解显存碎片。**

- **性能: 1186s/it (约 20 分钟/步)。极慢，且日志提示 hipBLASLt 不支持。**

**尝试 3 (双卡核弹级防死锁):**

- **调整: 禁用 P2P (NCCL_P2P_DISABLE=1)，禁用共享内存 (NCCL_SHM_DISABLE=1)，强制 Socket 通信。**

- **性能: 734s/it。**

- **分析: 通信通了，但效率极低。**

**最终突破 (系统层修复):**

- **根因: Intel Xeon CPU 与 AMD GPU 组合下，Linux 内核的 IOMMU 机制干扰了显卡间的 PCIe P2P 通信。**

- **解决: 修改 Grub 启动参数，添加 intel_iommu=off 并重启。**

- **结果: 彻底解除物理层通信限制，软件层可重新开启 P2P。**

### 阶段三：计算效率与算子优化

#### 现象: IOMMU 关闭后，GPU 利用率飙升至 100%，但速度依然很慢 (700s+/it)。

**分析:**

- **DeepSpeed Profiler 显示 fwd_microstep (前向计算) 耗时 224秒，而通信仅 74秒。**

- **瓶颈在计算而非通信。**

- **发现开启了 --bf16 True，但 MI50 (Vega20) 硬件不支持 BF16，驱动层进行低效模拟。**

**调整 1 (降级精度):**

- **操作: 切换为 --fp16 True。**

- **性能: 提升至 253s/it (提升约 3 倍)。**

- **新问题: 出现数值崩溃，grad_norm: NaN，loss: 0.0。**

**调整 2 (基准测试吞吐量):**

- **: 单卡，FP16，Batch=8，关闭 Gradient Checkpointing。**

- **性能: 24s/it (0.33s/sample)。**

- **结论: 硬件没问题，是 DDP + Gradient Checkpointing + 小 Batch 的组合导致了低效。**

### 阶段四：数值稳定性与最终方案 (The Golden Config)

#### 现象: FP16 虽然快，但在 Qwen2.5 模型上极其不稳定，必出 NaN。

- **最终决策: 放弃 FP16/BF16，回归 FP32 (全精度)。**

- **代价: 显存占用翻倍 (权重需 28GB+)，必须极致压缩 Batch Size。**

- **优势: 彻底解决 NaN 问题，无需量化即可训练。**

#### 最终配置策略 (空间换时间):

- **精度: FP32 (--fp16 False, --bf16 False)。**

- **显存管理:***

  - per_device_train_batch_size=1 (极限塞入)。

  - gradient_checkpointing=True (必须开启)。

  - 弃用 DeepSpeed ZeRO-2，改用原生 PyTorch DDP (减少老硬件上的额外开销)。

- **性能: 340s/it (约 5.5 分钟/步)。**

- **状态: Loss 正常下降 (1.4 -> 1.2)，Grad Norm 稳定 (2.0左右)。**

## 3. 关键知识库 (Knowledge Base)

- **IOMMU 陷阱: 在 Intel CPU + AMD GPU 的多卡机器上，必须在 BIOS 或 Grub 中关闭 IOMMU (intel_iommu=off)，否则 PCIe P2P 通信会死锁。**

- **MI50 精度支持: MI50 (Vega20) 不支持 BF16 硬件加速。强开 BF16 会导致百倍级减速。FP16 速度快但易溢出 (NaN)。FP32 最稳但显存占用大。**

- **Flash Attention: MI50 对 PyTorch 2.x 的 SDPA (Flash Attention) 支持不佳，建议显式禁用 (--flash_attn disabled)，使用 Vanilla Attention 反而兼容性最好。**

- **通信协议: IOMMU 关闭后，应优先使用 P2P。若必须调试死锁，NCCL_P2P_DISABLE=1 和 NCCL_SHM_DISABLE=1 是最有效的软隔离手段。**

## 4. 最终可运行脚本 (Final Train Script)

**此脚本基于 PyTorch Native DDP + FP32 全精度，适用于 MI50 32GB 双卡环境。**

```Bash
#!/bin/bash

# ================= 1. 硬件与环境适配 =================
# 伪装显卡架构，防止 ROCm 识别错误
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export ROCM_HOME=/opt/rocm
export HIP_VISIBLE_DEVICES=0,1
export DISABLE_VERSION_CHECK=1

# ================= 2. 通信配置 =================
# 假设已在系统层关闭 IOMMU，此处使用默认配置 (开启 P2P)
export NCCL_ASYNC_ERROR_HANDLING=1
export PL_TORCH_DISTRIBUTED_BACKEND=nccl

# ================= 3. 显存与算子优化 =================
# 缓解显存碎片 (对 FP32 尤为重要)
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
# 激活 MIOpen 自动搜索最优卷积算法
export MIOPEN_FIND_MODE=1 

# ================= 4. 路径配置 =================
MODEL_PATH="/home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct"
OUTPUT_PATH="./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32_final"

# ================= 5. 启动命令 =================
# 使用 torchrun 替代 deepspeed，减少 overhead
# 策略: FP32 保证数值稳定, Batch=1 + Checkpointing 应对显存压力
# 总 Batch Size = 1 * 2(卡) * 32(累积) = 64

torchrun --nproc_per_node=2 --master_port=29508 src/train.py \
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
    --max_grad_norm 0.5 \
    --num_train_epochs 5.0 \
    --lr_scheduler_type cosine \
    --warmup_ratio 0.1 \
    --fp16 False \
    --bf16 False \
    --logging_steps 1 \
    --save_steps 50 \
    --plot_loss True \
    --cutoff_len 1024 \
    --preprocessing_num_workers 16 \
    --gradient_checkpointing True \
    --flash_attn disabled \
    --ddp_timeout 18000000 \
    --optim adamw_torch \
    --resume_from_checkpoint False # 若需断点续训改为 True 并去掉 overwrite_output_dir
```

## 5. 运维指南

- **断点续训:**

  + 修改脚本中的 --overwrite_output_dir 为注释状态。

  + 添加/修改 --resume_from_checkpoint True。

  + 确保 saves 目录下有 checkpoint-xxx 文件夹。

- **监控: 使用 watch -n 1 rocm-smi 查看 GPU 状态。正常运行时 GPU 利用率应有波动（加载数据与计算交替），显存占用应在 30GB+。**

- **预期耗时: 当前配置下，每 50 步约需 4.5 小时。跑完 5 个 Epoch 预计需 40 小时左右。**
