# EntityAlias 实体别名子系统：设计、Wikidata 集成与容量评估

> 日期：2026-10-16
> 状态：已实施（含 Wikidata 在线建议源与查询时别名展开）
> 模块：`EntityAlias/`；IIS 配置挂在 `optional_subsystems.entity_alias` 下

---

## 1. 定位与架构

实体别名子系统维护"标准名 ⇄ 别名"映射，解决 OSINT 场景中同一实体的多种称呼
（多语言、简称、全称、旧称、错别字容忍）导致的检索碎片化。模块相对独立，
可整体禁用，理论上可拆为独立项目（仅依赖 Flask；向量化与在线建议均为可选插件）。

```
名字输入
  → 精确匹配（归一化 dict，O(1)）
  → 部分/模糊匹配（bigram 倒排预过滤 + SequenceMatcher/bigram Jaccard）
  → 可选向量召回（复用 VectorDB 服务独立集合 entity_alias_names）
  → 可选在线建议（Wikidata wbsearchentities）
AI 确认后注册回本地 JSONL（git 管理的事实源）
```

## 2. 数据模型（关键设计决策）

### 2.1 多语言标准名不是别名

每种语言有自己的**标准名**，该语言内的非正式叫法才是别名：

- `canonical` + `lang`：主标准名（通常中文）
- `localized: {lang: 该语言标准名}`：英文名/母语本名放这里
- `native_lang`：`resolve(name, lang="native")` 返回母语标准名；未覆盖语言回落主标准名
- `aliases[].lang` + `role`（abbr/full/former）：语言内的非正式叫法
- 所有名字（主标准名、各语言标准名、别名）进同一个全库唯一性索引，跨语言解析到同一实体

### 2.2 其它字段

- `type`：person / organization / country / location / animal / commodity / other / uncertain
- `status`：unverified（AI 注册默认）/ verified（已核实）——权威等级用字段而非文件划分，
  避免实体升级时跨文件移动污染 git diff
- `description`：纯 meta，不参与匹配，随 lookup 候选返回供 AI 消歧
- `refs`：外部标识（`{"wikidata": "Q9684"}`），更新时合并不替换

## 3. 存储设计：JSONL 文本事实源 + 内存查询层

- `EntityAlias/data/<type>.jsonl`：一行一个实体，按标准名排序，原子重写（tmp+replace）。
  可人工编辑、git diff 友好、随仓库版本化
- 运行时内存索引负责全部查询；启动时全量加载
- 向量数据在 VectorDB 服务（不落文本）；Wikidata 结果缓存在 `data/.cache/`（不入 git）

## 4. Wikidata 集成（本地优先，在线回落，补录回本地）

### 4.1 流程

```
本地 resolve/lookup 未命中
  → GET /api/suggest?name=X        （自动附带 Wikidata 候选）
  → AI 结合 description 确认
  → POST /api/entities/from_wikidata {"qid": ...}  （预填多语言数据补录）
```

补录脚本 `Scripts/entity_alias_backfill.py scan --enrich` 可离线批量预填候选。

### 4.2 API 政策与工程措施（2026 年现状）

- 免费、无需 Key、数据 CC0；2025-2026 年 Wikimedia 全线收紧限流
- 强制描述性 User-Agent；未识别 ~10 次/分，合规 UA ~200 次/分；429 须按 Retry-After 退避
- 本模块内置：默认 UA（可配）、固定节流 0.5s、429 退避重试 ×3、SQLite 结果缓存 7 天、
  代理可配（socks 需 PySocks）、网络失败静默降级
- 边界：Wikidata 只作注册时建议源，不进运行时查询链路；长尾实体覆盖不足，查不到不代表不存在

## 5. 容量评估（合成基准实测，2026-10-15，Windows/i7 单机）

| 实体量 | 名字数 | 启动加载 | 内存峰值 | 数据文件 | 精确解析 | 模糊查询* | 单写（含整文件重写） |
|---|---|---|---|---|---|---|---|
| 1 万 | 3 万 | 1.3s | 33MB | 4MB | 3µs | ~0.2s | 5ms |
| 5 万 | 15 万 | 8.6s | 172MB | 20MB | 3µs | 0.3–1.2s | 10ms |
| 10 万 | 30 万 | 17s | 335MB | 40MB | 4µs | 0.5–2.7s | 14ms |

\* 模糊区间：下限为真实多样性名字（bigram 预过滤生效），上限为合成极端数据
（所有名字共享大部分 bigram，预过滤失效的真实退化情况）。

实施中修复的性能 bug：索引 `remove_entity` 原为全表扫描（O(n)），导致启动重建 O(n²)
（5 万实体 161s）；改为 entity→keys 反向映射后 8.6s。

**拐点建议**：
- ≤5 万实体：当前设计完全够用（现实目标量级）
- 5–50 万：加 type 维度预过滤，或模糊查询换 trigram/SQLite FTS
- \>50 万：事实源切 SQLite（JSONL 降为导出格式），service 接口不变
- 已知取舍：整文件重写保证 git 友好，适合低频 AI 注册，不适合万级连续导入

## 6. 查询时别名展开（已接入主搜索）

EntityAlias 挂载后，组合根把 `expand_names` 注入各子系统 `EventV4QueryEngine.alias_expander`：
`peoples/locations/organizations` 过滤自动展开为该实体全部已知名字再 `$in` 查询。
归档零改动，别名表更新即时生效，未挂载时行为不变。

历史数据的"补录"对象是**别名表本身**（而非归档）：
`entity_alias_backfill.py scan`（扫归档实体字段→频次→未注册清单+候选）→ AI 填 `action`
→ `register` 执行。

## 7. 未来改进方向

按预期价值排序：

1. **实体写入侧标注（设计中的方案 B）**：归档 post-process 把 canonical 写入派生字段，
   使实体频率/聚类/图谱的服务端聚合受益；需配显式 backfill。先观察查询时展开是否够用
2. **AI 主动消歧**：lookup 候选 + description 交给 LLM 自动判断"是否同一实体"，
   高风险判断（低分候选合并）仍留人工/verified 流程
3. **reload API**：手工编辑 JSONL 后热加载（当前需重启）
4. **GeoNames 建议源**：地名覆盖优于 Wikidata，数据量小（~1.6GB），可本地化部署
5. **Wikidata dump 裁剪**：补录规模撞 API 限流时，流式过滤 latest-all.json.bz2
   生成本地名字表（几 GB），suggest 改查本地
6. **大规模化**：>50 万实体时 SQLite 事实源 + trigram FTS5 模糊查询
7. **审计与管理页**：变更历史（谁/何时/为何改）、Web 管理界面、`verified` 审核队列页
8. **与翻译子系统联动**：翻译产物中的实体名可作为别名注册候选来源

## 8. 配置参考

```yaml
optional_subsystems:
  entity_alias:
    enabled: true
    url_prefix: /entity-alias
    # data_dir: EntityAlias/data     # 空 = 包内 data/（git 管理）
    vector_enabled: true              # 复用 vector 子系统的 VectorDB
    wikidata:
      enabled: true
      # user_agent: "MyTool/1.0 (contact@example.com)"
      # proxy: ""                    # 空 = GlobalConfig.DEFAULT_PROXY
      min_interval_sec: 0.5
      timeout_sec: 10
      langs: zh,en
```
