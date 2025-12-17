# 1. 环境变量适配 MI50
export HSA_OVERRIDE_GFX_VERSION=9.0.6
export HIP_VISIBLE_DEVICES=0
# 显存碎片优化 (FP32模式下这很重要)
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True 

# 2. 启动 API (Checkpoint-100)
python src/api.py \
    --model_name_or_path /home/sleepy/Depot/ModelTrain/qwen/Qwen2___5-7B-Instruct \
    --adapter_name_or_path ./saves/qwen2.5-7b-intelligence/lora/sft_ddp_fp32/checkpoint-100 \
    --template qwen \
    --finetuning_type lora \
    --infer_dtype float32 \
    --trust_remote_code True
