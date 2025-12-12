在 LLaMA-Factory 的 data 目录下找到 dataset_info.json，在里面追加以下内容。

```
"intelligence_alpaca": {
    "file_name": "alpaca_train.json",
    "columns": {
      "prompt": "instruction",
      "query": "input",
      "response": "output"
    }
  }
```

把第三步生成的 alpaca_train.json 复制到 LLaMA-Factory 的 data 目录下。



将 ds_z2_config.json 放到 LLaMA-Factory 根目录下（针对 32GB 显存的双卡优化配置）。


Note：offload_optimizer 设为 none 表示把优化器状态留在 GPU 上。MI50 有 32G 显存，训练 7B 模型绰绰有余，放在 GPU 上速度最快。如果显存不足（OOM），可将其改为 "cpu"。






将 train_qwen.sh 放到LLaMA-Factory 根目录下，并赋予执行权限 (chmod +x train_qwen.sh)。









