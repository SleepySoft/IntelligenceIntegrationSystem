# Event V4 Prompt 版本归档

本目录集中保存 Event V4 各 Prompt 版本的**最终完整文本**及生产对比报告，避免构造器、
公共章节和灰度结论分散后难以还原某一版本实际发送给模型的内容。

## 目录

- [`prompts/`](prompts/)：`PromptCatalog` 完成全部章节拼装后的 v41—v53 静态文本；
  [`MANIFEST.md`](prompts/MANIFEST.md) 记录协议、抽样状态、长度和 SHA-256。
- [`reports/`](reports/)：各轮设计、灰度和生产评估报告，按日期及版本命名。

完整 Prompt 仍保留 `{{CURRENT_DATE}}`、`{{CONTENT}}`、`{{SIMILAR_MESSAGES}}` 等占位符；
这些值只在单次请求前注入，因此不属于可提交的版本静态文本。

## 更新规则

Prompt 的单一事实源仍是 `prompts_event_v4.py` 及各版本构造器。修改、增加或调整抽样版本后
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
