================================================================
                    LLM 微调参数与策略笔记
               Project: Intelligence Integration
================================================================

[硬件环境]
- GPU: AMD Radeon Instinct MI50 (32GB) x 2
- 精度: BF16 (BFloat16) - MI50 对此支持良好，优于 FP16（不易溢出）。
- 策略: DeepSpeed ZeRO-2 (双卡并行 + 优化器状态切分)。

[关键超参数解读]

1. LoRA Rank (r) = 64
   - 含义: LoRA 矩阵的秩，决定了可训练参数量。
   - 设定理由: 你的任务包含复杂的评分逻辑(0-10分)和结构化提取，需要较高的“智商”。
   - 调节: 如果模型学不会评分规则，可尝试加到 128；如果训练太慢，降回 32。

2. LoRA Alpha = 128
   - 含义: LoRA 更新的缩放系数，通常设为 Rank 的 2 倍。
   - 作用: 类似于学习率的放大器。

3. Learning Rate = 5e-5
   - 含义: 学习率。
   - 调节: 
     - 如果 Loss 曲线下降极慢: 尝试 1e-4。
     - 如果 Loss 曲线震荡或无法收敛: 降至 2e-5。

4. Epochs = 5
   - 含义: 全量数据遍历次数。
   - 设定理由: 数据量较少(约几千条)，需要多轮次让模型“吃透”规则。
   - 观察: 如果 Val Loss 在第 3 轮后开始上升，说明过拟合了，取第 3 轮的权重即可。

5. Cutoff Length = 4096
   - 含义: 上下文最大长度。
   - 风险: 显存占用的最大元凶。
   - 应对: 如果遇到 OOM (Out Of Memory)，优先把这个砍半 (2048)。

[Ollama 部署贴士]

1. 模型合并
   训练完成后，你得到的是 LoRA 权重(适配器)。
   需要使用 LLaMA-Factory 的 export_model.py 将其与原版 Qwen2.5 合并导出为完整的 .safetensors 或 .gguf，才能给 Ollama 用。

2. Modelfile 设置
   在创建 Ollama 模型时，Modelfile 必须包含你的 SYSTEM_PROMPT。
   
   示例 Modelfile:
   FROM ./merged_model_qwen.gguf
   TEMPLATE """{{ if .System }}<|im_start|>system
   {{ .System }}<|im_end|>
   {{ end }}{{ if .User }}<|im_start|>user
   {{ .User }}<|im_end|>
   {{ end }}{{ if .Assistant }}<|im_start|>assistant
   {{ .Assistant }}<|im_end|>
   {{ end }}"""
   SYSTEM """你是一个专业情报分析师... (此处粘贴你的完整 Prompt)"""
   PARAMETER stop "<|im_end|>"

3. 开启 JSON 模式
   在代码调用 Ollama 时，务必设置 format='json'。
   curl http://localhost:11434/api/generate -d '{
     "model": "my-intelligence-model",
     "format": "json",
     "prompt": "..."
   }'

[常见问题排查]

- 显存溢出 (OOM):
  1. 降低 per_device_train_batch_size (如 4 -> 2)。
  2. 降低 cutoff_len (4096 -> 2048)。
  3. 开启 DeepSpeed 的 cpu_offload (修改 ds_config.json)。

- 模型只会复读/不输出 JSON:
  1. 检查 dataset_info.json 里的 columns 映射是否对齐了 input/output。
  2. 检查 --template qwen 是否写对。

- 评分逻辑混乱:
  1. 检查 Alpaca 数据中是否存在大量“评分冲突”的样本。
  2. 增加 Epochs。
  3. 增加 LoRA Rank。
