# EntityAlias — 实体别名子系统

标准名 ⇄ 别名 映射服务。相对独立，可整体禁用；通过 Flask Blueprint 挂载到 IIS，
也可以作为独立项目使用（仅依赖 Flask，可选对接任意向量服务）。

## 功能

1. **标准名解析**：传入任意已知名字（标准名或别名，任意语言），返回标准名
2. **批量接口**：`resolve_batch` / `lookup_batch`
3. **模糊与部分查询**：精确未命中时，按归一化包含 / 编辑距离 / bigram 召回候选
4. **可选向量召回**：再未命中时用语义相似度查找最近的名字（IIS 集成时复用
   VectorDB 服务的独立集合 `entity_alias_names`；模块本身不依赖 VectorDB）
5. **REST API + 管理页**：`/entity-alias/manage` 提供全局实体与固定别名增删改查、主标准名
   切换；写操作及管理页的登录保护由挂载方注入

管理页与实体图编辑器相互独立。主标准名从已有别名或语言标准名中提升，旧主标准名会自动
保留为别名或对应语言标准名。删除实体目前不会级联清理 EntityResolution 的既有指向，页面
会明确提示这一风险；在引用检查/重定向实现前应谨慎删除。

## 两个设计决策

### 1. 多语言标准名：英文名 / 母语本名不是别名

每种语言有自己的**标准名**，该语言内的非正式叫法才是别名：

- `canonical`：主标准名（通常中文，`lang` 标注）
- `localized`：`{lang: 该语言的标准名}`，如 `"en": "The New York Times"`
- `native_lang`：母语代码（`resolve/lookup` 传 `lang=native` 时返回母语标准名）
- `aliases[].lang` 表示别名所属语言，`role` 表示性质：`abbr` 简称 / `full` 全称 / `former` 旧称
- 所有名字（主标准名、各语言标准名、别名）都进同一个唯一性索引，跨语言解析到同一实体

解析时按语言切换：`resolve?name=X&lang=en` 返回英文名，`lang=native` 返回母语名，
未覆盖的语言回落主标准名。

### 2. 存储：文本为事实源，内存为查询层

- **事实源**：`EntityAlias/data/<type>.jsonl`，一行一个实体，按标准名排序，
  原子重写（tmp + replace）——git diff 最小、可直接人工编辑、随仓库版本化。
- **查询层**：启动时全量加载到内存索引（精确 dict + 模糊扫描），千级规模下
  模糊全表扫描也是毫秒级；写操作 = 内存更新 + 整文件重写。
- **向量数据**：完全放在 VectorDB 服务（独立集合），不落文本、不入 git。

这里仅保存全局 `ent_*`。非全局语境实体 `ctx_*` 位于各业务子系统 MongoDB 的
`<prefix>contextual_entities`；具体情报指称到全局/语境实体的关联分别位于
`<prefix>entity_global_resolutions` 和 `<prefix>contextual_entity_resolutions`，关联主键由
`subsystem + intelligence_uuid + EVENT_EXTRACTION 内 ENT ID` 构成。它们属于
`EntityResolution`，不会由本管理页修改。

当前没有另一张可跨情报复用的“非全局别名表”：上下文相关的原始称呼以 `surface` 快照保存
在每条 resolution 记录中。它若指向稳定对象，目标是 JSONL 中的 `ent_*`；若指向泛称或临时
群体，目标是 `contextual_entities` 中的 `ctx_*`。因此同一个称呼可以在不同情报中指向不同目标。

## 数据格式（JSONL 每行）

```json
{
  "id": "ent_5f2a1b9c8d3e",
  "canonical": "人民日报",
  "lang": "zh",
  "type": "organization",
  "status": "verified",
  "description": "中共中央机关报",
  "aliases": [
    {"name": "People's Daily", "lang": "en", "role": "native"},
    {"name": "人民网", "lang": "zh", "role": ""}
  ],
  "created_at": "...", "updated_at": "..."
}
```

- `type`：`person / organization / country / location / animal / commodity / other / uncertain`
- `status`：`unverified`（AI 注册默认）/ `verified`（已核实）。权威等级是**字段而非文件划分**，
  避免实体升级时跨文件移动
- `refs`：外部标识字典（如 `{"wikidata": "Q9684"}`），在线补录的出处溯源；更新时合并而非替换
- 别名 `role`：空 / `abbr`（简称）/ `full`（全称）/ `former`（旧称）。
  全称简称一律做成别名而非 meta 字段——它们需要在匹配链中可解析；
  外语/母语的“标准名”放 `localized` 而非别名
- `description`：纯 meta（不参与匹配），供 AI 在 lookup 候选中判断是否同一实体
- 约束：一个（归一化后的）名字全库只能属于一个实体；注册冲突返回 409

## 在线建议源（Wikidata，本地优先）

查询顺序：本地精确 → 本地模糊/向量 →（仍未命中且配置了在线源）Wikidata `wbsearchentities`。
AI 确认候选后调 `POST /api/entities/from_wikidata {"qid": "Q..."}` 将其多语言 label/别名/简介
预填注册回本地（带 `refs.wikidata` 溯源）。

工程约束已内置于 `wikidata.py`：描述性 UA、固定节流（默认 0.5s）、429 按 `Retry-After` 退避、
SQLite 结果缓存（7 天，`.cache/` 不入 git）、代理可配（默认 `GlobalConfig.APPLIED_PROXY`，
socks 代理需 PySocks）。网络失败一律静默降级为空结果，不影响本地功能。

历史补录：`Scripts/entity_alias_backfill.py scan --enrich` 会查询 Wikidata。若只有一个
候选的 `matched` 或 `label` 与输入名归一化后完全相同，脚本会直接按 QID 拉取
多语言名称、别名、简介和 `refs.wikidata` 并写入本地；多义/模糊候选仍留给
AI 处理。使用 `--no-auto-register-exact` 可只生成候选而不写入。

## 容量评估（合成基准实测，2026-10）

| 规模（实体） | 名字数 | 启动加载+索引 | 内存峰值 | 数据文件 | 精确解析 | 模糊查询* | 单次写入（含整文件重写） |
|---|---|---|---|---|---|---|---|
| 1 万 | 3 万 | 1.3s | ~33MB | 4MB | 3us | ~0.2s | 5ms |
| 5 万 | 15 万 | 8.6s | ~172MB | 20MB | 3us | ~0.3–1.2s | 10ms |
| 10 万 | 30 万 | 17s | ~335MB | 40MB | 4us | ~0.5–2.7s | 14ms |

\* 模糊查询区间下限为真实多样性名字（bigram 倒排预过滤生效），上限为本基准的极端合成数据
（所有名字共享大部分 bigram，预过滤失效的真实退化情况）。

结论与拐点：
- **≤5 万实体：当前设计（JSONL + 内存索引）完全够用**，这也是本系统实体表的现实目标量级
- 5–50 万：模糊查询和内存开始紧张；优化方向是加 `type` 维度的预过滤、或模糊查询换 trigram SQLite FTS
- **>50 万实体**：应把事实源切到 SQLite（JSONL 降为导出格式），整个 service 层接口不变
- 已知设计取舍：写入 = 整文件原子重写（git diff 友好的代价）。日常 AI 注册是低频写，无影响；
  批量补录数百实体也毫无压力，但不要用它做万级连续写入的导入通道

挂载后，IIS 的实体过滤查询（`peoples` / `locations` / `organizations`）会自动把每个名字
展开为其所属实体的全部已知名字再查 Mongo `$in`：归档零改动，别名表更新即时生效。
历史数据的处理分为两个明确步骤：

1. `Scripts/entity_alias_backfill.py scan [--enrich]` 扫描未注册名称并补全
   EntityAlias JSONL 事实库；该脚本不回写 MongoDB 归档。
2. `Scripts/entity_alias_archive_backfill.py` 预览已有归档的标准名变更；确认后使用
   `--apply` 回写权威归档、三类速查字段、outbox 和 `v4_events`。实际写入前
   会在 `_export/` 生成 BSON Extended JSONL 原文档备份。

子系统启用且初始化成功时，组合根还会把同一服务实例注入 Event V4 分析后处理：
协议桥接与校验完成后，对 `EVENT_EXTRACTION.entities[].name` 做本地精确解析，
命中时替换为 `canonical`，未命中或解析异常时保留 LLM 原始名称。规范名会继续
进入权威归档、事件派生索引及 `PEOPLE/LOCATION/ORGANIZATION` 速查字段；
关闭 EntityAlias 或初始化失败不会改变分析主链行为。

## 挂载到 IIS

```python
from EntityAlias.iis_integration import mount_to_iis
mount_to_iis(app, config=config, wrapper=access_manager.login_required,
             vector_client=vector_db_client_or_none)
```

配置（IIS 主配置 YAML，挂在 `optional_subsystems` 下；未出现的字段由 IISConfig 提供默认值）：

```yaml
optional_subsystems:
  entity_alias:
    enabled: true
    url_prefix: /entity-alias
    # data_dir: EntityAlias/data
    vector_enabled: true
    wikidata:
      enabled: true
      min_interval_sec: 0.5
      timeout_sec: 10
      langs: zh,en
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/entity-alias/api/resolve?name=X` | 精确解析标准名 |
| POST | `/entity-alias/api/resolve_batch` | 批量解析 `{names: [...]}` |
| GET | `/entity-alias/api/lookup?name=X&limit=5` | 精确 + 模糊/部分 + 向量候选 |
| POST | `/entity-alias/api/lookup_batch` | 批量候选查询 |
| GET | `/entity-alias/api/entities?type=&q=` | 实体列表 |
| POST | `/entity-alias/api/entities` | 注册实体（登录） |
| GET/PUT/DELETE | `/entity-alias/api/entities/<id>` | 查询/更新/删除（写需登录） |
| POST/DELETE | `/entity-alias/api/entities/<id>/aliases` | 别名增删（登录） |
| POST | `/entity-alias/api/entities/<id>/canonical` | 将已有别名/语言标准名提升为主标准名（登录） |
| POST | `/entity-alias/api/rebuild_vector` | 全量重建向量索引（登录） |
| GET | `/entity-alias/api/status` | 状态 |

AI 调用方的工作流程见 `skill/SKILL.md`。
