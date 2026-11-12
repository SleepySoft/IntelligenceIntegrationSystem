# EntityAlias V2（实验实现）

V2 是与现有 `EntityAlias/` 完全并行的 SQLite 实现。目前不装配到 IIS 主链，不读取、迁移或
修改 V1 JSONL。未来切换时可以直接丢弃 V1 别名数据，从空 V2 数据库重新建立事实库。

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

## 当前边界

- 没有 V1 导入器，这是有意设计。
- 没有 Web/API、模糊/向量查询。
- 没有上下文规则引擎；V2 遇到 `context_required` 会交还给上层。
- 切换生产前需要单独设计 EntityResolution 适配器；shadow 不会进入正式查询或图。

## IIS Shadow

`optional_subsystems.entity_alias_v2_shadow.enabled` 打开后，组合根只安装归档观察扩展：

- V2 事实库：`_data/entity_alias_v2/entities.db`
- 影子观察与补录 ledger：`_data/entity_alias_v2/shadow.db`
- Wikidata 缓存：`_data/entity_alias_v2/wikidata_cache.sqlite`
- 内存队列：`EntityAliasV2ShadowEnrichment`

以上资源都不与 V1 共享。组合根不会把 V2 注入别名查询展开、EntityResolution、态势图、
Web 管理页或权威归档。Wikidata 唯一精确同名候选只会创建 `unverified` 实体；Wikidata aliases
默认标为 `context_required`。同一 QID 后续出现不同 canonical type 时记为 `type_conflict`，
不会修改已有实体类型或建立第二个实体。补录终态写入持久化 ledger，服务重启不会重复查询。

只读查看影子进度：

```bash
python Scripts/entity_alias_v2_shadow_report.py
```

运行测试：

```bash
pytest -q Test/test_entity_alias_v2.py Test/test_entity_alias_v2_shadow.py
```
