# EntityAlias V2（事实库、影子观察与正式解析）

V2 是与现有 `EntityAlias/` 数据隔离的 SQLite 实现，不读取、迁移或修改 V1 JSONL。它可按
`shadow / prefer_v2 / v2_only` 三种模式接入 EntityResolution；推荐先用 `prefer_v2`，让 V2
处理满足安全策略的候选，其余回退 V1。

## 核心模型

- 实体拥有唯一稳定的 `canonical_type`。
- canonical、localized name 和 aliases 都属于同一实体，不各自保存类型。
- 加载/查询时派生 `(normalized_name, canonical_type) -> candidates[]` 类型化索引。
- 同一个名称可以属于不同类型的实体，也可以属于多个同类型实体。
- `EntityID` 才是身份键；类型化名称只是候选召回键。
- `mention_type` 是单次抽取值，先规范化为 `canonical_type` 再召回。
- V2 只做候选召回和保守精确解析；角色、谓词、正文等上下文判断仍属于
  `EntityResolution`。

## 为什么使用 SQLite

V2 不再以 git 可审阅的纯文本为目标。SQLite 提供事务、唯一约束、索引、外键和原子更新，
也允许后续扩展批量导入、审核状态、候选统计和全文/模糊检索，而不需要整文件重写。

只有显式启用的 shadow 才会创建数据库。独立调用方也可以直接选择路径：

```python
from EntityAliasV2 import EntityAliasV2Service, SQLiteAliasV2Store

store = SQLiteAliasV2Store("_data/entity_alias_v2.db")
service = EntityAliasV2Service(store)
```

## 保守解析状态

`service.resolve(name, mention_type=...)` 返回：

| status | 含义 |
|---|---|
| `resolved` | 唯一、已核实、可直接使用的同类型候选 |
| `ambiguous` | 缺少类型，或同名同类型存在多个候选 |
| `type_conflict` | 名称存在，但没有同类型候选 |
| `context_required` | 唯一别名绑定仍明确要求上下文判断 |
| `unverified` | 唯一候选未经核实 |
| `not_found` | 名称不存在 |
| `invalid_type` | 抽取类型无法规范化 |

除 `resolved` 外，服务均不会给出最终身份。`allow_unverified=True` 仅供显式实验使用。

## 存储结构

- `entities`：实体 ID、canonical type、状态与描述。
- `entity_names`：名称、语言、名称种类、角色和是否要求上下文。
- `entity_refs`：Wikidata 等外部 ID，并保证 `(namespace, value)` 全局唯一。
- `meta`：V2 schema 版本。

数据库约束只禁止同一实体内部的重复名称。跨实体重名是合法数据，查询结果自然成为候选集。

## 正式解析策略

事实层的 `verified/unverified` 与单次名称绑定是两个判断。生产适配器当前只接受：

- V2 原本已返回 `resolved` 的唯一直接名称；
- 唯一、类型匹配、带 Wikidata QID 的直接名称，即使实体仍是 `unverified`；
- 唯一、带 Wikidata QID、长度不少于 2 且请求携带 ISO 国家上下文的人物简称。

同名同类型多候选、类型冲突、缺少类型、缺少国家上下文的人物简称，以及所有
`context_required` 的组织/地点/转喻仍然弃权。这个规则是首版安全边界，不会批量改写事实库，
后续可根据具体误合并/漏合并样本调整。

配置示例：

```yaml
optional_subsystems:
  entity_alias_v2:
    mode: prefer_v2
    trust_wikidata: true
    contextual_person_aliases: true
    require_country_for_contextual_person: true
    min_contextual_name_length: 2
```

模式含义：

- `shadow`：V2 不进入正式解析；
- `prefer_v2`：V2 命中优先，未命中或弃权时回退 V1；
- `v2_only`：正式解析只使用 V2，适合完成观察后收口。

## 当前边界

- 没有 V1 导入器，这是有意设计。
- 没有 Web/API、模糊/向量查询。
- 上下文策略目前只处理带国家码的唯一人物别名；复杂语义仍交还给人工解析。
- 查询时别名展开和管理 Web 仍由 V1 承担；V2 正式入口当前是 EntityResolution 及其实体图消费者。

## IIS Shadow

`optional_subsystems.entity_alias_v2_shadow.enabled` 打开后，组合根安装归档观察扩展：

- V2 事实库：`_data/entity_alias_v2/entities.db`
- 影子观察与补录 ledger：`_data/entity_alias_v2/shadow.db`
- Wikidata 缓存：`_data/entity_alias_v2/wikidata_cache.sqlite`
- 内存队列：`EntityAliasV2ShadowEnrichment`

以上资源都不与 V1 共享。是否注入 EntityResolution 仅由 `entity_alias_v2.mode` 决定，shadow
开关本身不会改变正式结果。V2 不注入查询展开、Web 管理页或权威归档。Wikidata 唯一精确同名候选只会创建 `unverified` 实体；Wikidata aliases
默认标为 `context_required`。同一 QID 后续出现不同 canonical type 时记为 `type_conflict`，
不会修改已有实体类型或建立第二个实体。补录终态写入持久化 ledger，服务重启不会重复查询。

只读查看影子进度：

```bash
python Scripts/entity_alias_v2_shadow_report.py
```

运行测试：

```bash
pytest -q Test/test_entity_alias_v2.py Test/test_entity_alias_v2_production.py Test/test_entity_alias_v2_shadow.py
```
