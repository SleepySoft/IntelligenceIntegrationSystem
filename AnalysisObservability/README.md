# AnalysisObservability

分析质量、调用成本和 Prompt Cache 观测子系统。调用事实通过 `request_id` 与分析尝试关联，
缓存统计默认按“服务商 × 模型 × 情报域 × Prompt 版本”展示。

冷启动口径严格依赖供应商响应：只有明确返回缓存字段且 `cached_input_tokens == 0` 的调用
才计为冷启动；未返回缓存字段的调用单列为“未报告”，不会污染冷启动分母。页面同时展示
冷启动次数/比例、命中调用率、缓存 Token 比例、缓存观测覆盖率及时间趋势。

独立的 AI 任务效果观测层。它把宿主记录的“任务尝试/终态”和客户端记录的
“一次模型调用事实”按 `request_id` 关联，生成成功率、错误、延迟、Token、缓存与
忙碌时间产出等指标。模块不依赖 IIS 领域模型，也不要求使用 AIClientCenter。

宿主需要写入 `AnalysisStatsRecorder`；调用层只需提供兼容的 `ai_calls` SQLite 表，
至少包含 `request_id`、模型/客户端、耗时和 Token 字段。页面可挂到任意 Flask 应用：

```python
from AnalysisObservability.web import create_blueprint

app.register_blueprint(create_blueprint(
    analysis_db_path="data/task_stats.db",
    call_db_path="data/ai_calls.db",
    wrapper=login_required,       # 可选
), url_prefix="/analysis-observability")
```

独立页面为 `/analysis-observability/`，JSON API 为
`/analysis-observability/api/report?hours=24&dimension=model`。可选维度：
`model`、`client`、`service`、`prompt`、`model_prompt`。传 `embed=1` 可嵌入宿主页。

“忙碌小时产出”以所有关联调用的实际耗时之和为分母，适合间歇性工作负载；它不是
墙钟吞吐量，并行调用的耗时会累加。历史上没有 `request_id` 的记录仍展示任务效果，
但 Token/缓存/客户端维度记为未关联，并通过关联覆盖率显式标注。
