# Event V4 Prompt 与历史评估

本目录保存当前 Event V4 Prompt 的**最终完整文本**及历史生产对比报告。

## 目录

- [`prompts/`](prompts/)：`PromptCatalog` 完成全部章节拼装后的当前 v48 静态文本；
  [`MANIFEST.md`](prompts/MANIFEST.md) 记录协议、抽样状态、长度和 SHA-256。
- [`reports/`](reports/)：各轮设计、灰度和生产评估报告，按日期及版本命名。

当前运行目录只注册 v48，生产抽样、手工选择和导出均只有该版本。v48 的 system prompt
保持静态，正文、当前日期及相似消息由独立 user message 在请求时注入，以维持前缀缓存命中。
旧版本的可执行构造器和静态文本已经清理；`reports/` 继续保留其设计和灰度结论作为历史依据。

2026-10-25 的 31 篇 DeepSeek 离线实验进一步比较了 v48 单阶段与“事实召回→规范编码”
两阶段。两阶段在配对成功样本中表现出更高的实体、角色和时间保留趋势，但最终成功率
93.5%→64.5%、首过率 58.1%→25.8%、全链路 Token 增加 45.0%，不能直接生产化。下一轮
应先引入人工核心标注、参与者守恒校验和困难样本条件触发；该实验未改变当前 v48 生产目录。

## 更新规则

Prompt 的单一事实源是 `prompts_event_v4.py`。修改 v48 后
执行：

```bash
python Scripts/export_prompt_versions.py
python Scripts/export_prompt_versions.py --check
pytest -q Test/test_prompt_exports.py Test/test_prompt_protocols.py
```

不要直接编辑 `prompts/vNN.txt`、`prompts/MANIFEST.md` 或 `prompts/manifest.json`。
回归测试会检查提交的完整文本是否与运行时目录逐字一致。

## 报告索引

- [v41 / v42 真实模型对比](reports/20261012_prompt_v41_v42_comparison.md)
- [v43 生产评估](reports/20261014_prompt_v43_production_eval.md)
- [v49 独立精简设计与首轮结果](reports/20261020_prompt_v49_compaction.md)
- [v50 角色白名单灰度](reports/20261020_prompt_v50_role_guard_gray.md)
- [v51 单变量压缩灰度](reports/20261021_prompt_v51_role_compaction_gray.md)
- [v52 灰度设计](reports/20261021_prompt_v52_gray_design.md)
- [v48 / v52 完整生产灰度分析](reports/20261024_prompt_v48_v52_gray_analysis.md)
- [v53 完整度优先的收敛设计](reports/20261024_prompt_v53_converged_design.md)
- [v48 / v53 生产灰度首检查点](reports/20261024_prompt_v48_v53_first_checkpoint.md)
- [DeepSeek 单阶段与召回—编码两阶段离线实验](reports/20261025_deepseek_recall_two_stage_experiment.md)
