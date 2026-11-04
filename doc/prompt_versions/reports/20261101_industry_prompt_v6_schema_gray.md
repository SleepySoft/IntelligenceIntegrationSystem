# Industry Prompt v6：基于 v4 的闭集 Schema 灰度

> 启用时间：2026-11-01（Asia/Shanghai）<br>
> 状态：v4 是唯一正式版本；v6 是 25% 同模型 Shadow 候选；v5 退出当前 Shadow 但保留历史记录。

## 1. v5 结论

严格排除两条历史 v2→v5 污染记录后，共有 8 个真正的 v4/v5 Shadow 任务：v4 首次通过 6/8，
v5 首次通过 4/8；v5 最终仅形成 6 组可比较结果。成功结果的字段长度均在 v5 预算内，因此失败
不是由标题或说明文字过长造成。

两条耗尽三次重试的样本暴露了闭集 Schema 问题：

- `774dc475…` 三次重复输出协议外的 `attributes.用途`，并使用不存在的实体类型 `market`；
- `2081cc1f…` 先把 `duration.type` 写成 `number`，纠正后又新增协议外顶层字段
  `SUB_CATEGORY_NOTE`，最后一次仍保留该字段。

v5 的长度预算和 qualifier 扩写没有命中主要失败面，且增加了相对 v4 的指令量。因此 v5 不转正，
也不作为 v6 的文本基线。

## 2. v6 的唯一变更面

v6 直接调用 `build_industry_prompt_v4()`，只在“有价值输出结构”前插入一段闭集 Schema 自检。
准入、分类、八维评级、评分、事件优先级、谓词目录、字段长度和结果协议全部继承 v4。

新增规则只有四组：

1. 有价值结果顶层键采用明确白名单，禁止输出 `SUB_CATEGORY_NOTE` 或任何备注字段，即使值为空；
2. attributes 只允许八个协议键，资金用途、原因、备注和描述不得自造为属性；
3. 明确各 attributes 键对应的固定 type，尤其 `duration.type=duration`；
4. 实体 type 必须来自协议闭集，市场/赛道/行业使用 `topic`，公司使用 `organization`，禁止
   `market`、`industry`、`company` 等自造类型。

v6 不继承 v5 的 24/80/1600 字符预算，也不重复添加 qualifier、容器和空对象规则。这样可以把
实验变量限定为“闭集 Schema 提示是否提高首过率”。

## 3. 版本与运行配置

- Prompt Catalog：正式池 `[4]`；可选择历史/实验版本 `[1,2,3,4,5,6]`；
- industry Shadow：`versions=[6]`、`sample_rate=0.25`、`comparison_mode=same_model`；
- Shadow 只接受当前正式池 v4 作为主链；历史 cache 的 v1/v2/v3/v5 不参与 v6 配对；
- 同模型重试若最终客户端或模型漂移，结果不写入评估资产库。

现有 v4/v5 记录原样保留。v6 启用后不得把 v5 历史记录混入 v4/v6 汇总。

## 4. 首检查点

至少取得 12 个 v4/v6 任务或 8 个最终配对结果后检查：

- v6 首过率不低于 v4，且不再出现本报告列出的四类错误；
- v6 最终成功率不低于 v4，不以静默丢字段换取通过；
- TAXONOMY、总分、八维 RATE、主事件和事件数量相对 v4 不应系统性漂移；
- 未登记角色、角色类型冲突、谓词接地与实体数量不得明显恶化；
- 只统计 `primary_prompt_version=4`、`shadow_prompt_version=6` 且最终模型相同的样本。

只读核查命令：

```bash
python Scripts/compare_shadow_records.py \
  --domain industry --primary-version 4 --shadow-version 6 --same-model-only
```

若出现分类/评分系统性漂移，或 v6 首过率仍低于 v4，则停止 v6 Shadow，保留 v4 正式版本，不通过
继续堆叠提示文字修补。
