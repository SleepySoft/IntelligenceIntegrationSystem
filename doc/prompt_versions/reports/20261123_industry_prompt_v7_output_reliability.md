# Industry Prompt v7 输出可靠性三臂 Shadow

> 配置日期：2026-11-23（Asia/Shanghai）
> 启用时间：2026-11-23 16:59（Asia/Shanghai）
> 实验 ID：`industry-v2-v7-output-20261123`
> 正式版本：v2
> Shadow 版本：v2、v7
> 抽样：25% 确定性抽样；`comparison_mode=replicate_and_compare`

冻结文本：v2 为 26195 字符，SHA-256
`4c0187de5165d6260384ef7987b45217a8015caeebcacb2d57398da4f890b2d0`；v7 为 26397 字符，
SHA-256 `a755b1059bcd8d94dceb36c23c9e5269c7f8fcebd25c72a63f761feded979d55`。

## 目的

v2/v2 A/A 首个检查点显示：产业域正式 v2 的累计首过率约 62%，最近 7 天有价值分支首过率
仅约 49.5%；A/A 配对的 60 次 Shadow Schema 失败中，49 次涉及文字字段超长。当前完整 JSON
重试又会重新生成分类、RATE、事件和实体，放大原本的格式错误。

v7 不尝试改善产业判断语义，只验证能否在保持 v2 结果分布的前提下提高输出可靠性。三臂设计让
同一输入同时产生：

```text
正式 v2-A
Shadow v2-B（自然噪声参照）
Shadow v7（输出可靠性候选）
```

当 v2-A 与 v2-B 一致而 v7 不同时，差异更可能由候选 Prompt 引入；当两个 v2 自身不一致时，
该样本优先计入模型噪声而不是候选效果。

## v7 单变量边界

v7 直接从 v2 构造，完整保留：

- 公司行动、经营状态冲击、经营相关冲突和可执行产业政策四类准入；
- v2 的跨类型评级校准、八维 RATE 词表及评分映射；
- v2 的产业事件消歧与 Event Engine 核心子集协议。

只原位收紧以下输出预算，不重复新增一套长度规则：

1. `EVENT_TITLE` 24 字符，`REASON/EVENT_BRIEF/IMPACT/TIPS` 80 字符，`EVENT_TEXT` 1600 字符，
   为服务端 30/100/2000 上限预留余量；
2. 低价值 `REASON` 同样从 100 字符收紧为 80 字符。

除原位替换外，只增加三条最终检查：不得因压缩改变分类、RATE、决定性事实或阶段；
`predicate.surface` 必填且禁止旧 `agentive`/错误 frame 位置；角色使用非空实体数组、数值进入
attributes、空对象省略，并禁止自造后文协议外的键或类型。完整闭集继续由既有 Event Engine 协议
声明，v7 不再重复列举。

v7 明确禁止为了满足长度和 Schema 改变 TAXONOMY、RATE、决定性事实或事件阶段。它不继承 v3/v4
的准入门槛、评级纪律和事件选择文本，也不等同于历史 v6。

## 配置与资产隔离

`_config/domains/industry.json` 使用：

```json
{
  "versions": [2, 7],
  "sample_rate": 0.25,
  "comparison_mode": "replicate_and_compare",
  "experiment_id": "industry-v2-v7-output-20261123"
}
```

抽样仍由 `SHA256(experiment_id + domain_name + UUID)` 决定。命中一次后同时执行两个 Shadow，
预计额外 AI 调用基线约为正式流量的 50%，实际还会叠加校验重试。两个 Shadow 均定向到主链最终
客户端，并在最终客户端名或模型漂移时拒绝写入资产。

旧 `industry-v2-aa-20261112` 资产保留且不改写。IIS 核心服务已于 2026-11-23 16:59 重启，
启动日志确认 `Mongo shadow analysis enabled by domain: {'industry': [2, 7]}`，新配置自该时刻生效。

## 首个检查点与判定

至少取得 100 组同时具备 v2-A、v2-B、v7 的严格配对后评估：

1. v2-B 与 v7 的首过率及配对 McNemar 检验；
2. 文本超长、闭集 Schema、事件 frame/角色/attributes 错误的分类计数；
3. v2-A/v2-B 的 A/A 分类翻转与 v7 相对两个 v2 一致结果的额外偏离；
4. RATE 总分、主事件谓词、完整谓词集合、实体集合和抽取质量；
5. 额外调用次数、延迟及最终失败率。

建议准入线：

- v7 有价值分支首过率至少 75%，总体首过率至少 80%，且配对改善显著；
- 文字超长错误相对 v2-B 减少至少 80%；
- 在 v2-A 与 v2-B 分类一致的样本中，v7 的额外分类偏离不超过 3%；
- RATE 平均变化接近 0，主事件谓词一致率不低于 90%；
- 最终成功率、角色冲突和实体集合相似度不得回退。

本轮只决定是否采用 v7 的输出纪律，不得据此宣称产业准入或评分语义得到改进。

## 只读检查

分别汇总 A/A 复本与候选：

```bash
python Scripts/compare_shadow_records.py \
  --domain industry --primary-version 2 --shadow-version 2 \
  --experiment-id industry-v2-v7-output-20261123 \
  --same-model-only --summary-only

python Scripts/compare_shadow_records.py \
  --domain industry --primary-version 2 --shadow-version 7 \
  --experiment-id industry-v2-v7-output-20261123 \
  --same-model-only --summary-only
```
