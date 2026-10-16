# EntityAlias — 实体别名子系统

标准名 ⇄ 别名 映射服务。相对独立，可整体禁用；通过 Flask Blueprint 挂载到 IIS，
也可以作为独立项目使用（仅依赖 Flask，可选对接任意向量服务）。

## 功能

1. **标准名解析**：传入任意已知名字（标准名或别名，任意语言），返回标准名
2. **批量接口**：`resolve_batch` / `lookup_batch`
3. **模糊与部分查询**：精确未命中时，按归一化包含 / 编辑距离 / bigram 召回候选
4. **可选向量召回**：再未命中时用语义相似度查找最近的名字（IIS 集成时复用
   VectorDB 服务的独立集合 `entity_alias_names`；模块本身不依赖 VectorDB）
5. **REST API + 管理页**：`/entity-alias/`（ Blueprint，登录保护由挂载方注入）

## 两个设计决策

### 1. 英文名 / 母语本名的定位

**标准名和别名都带语言标签。**

- 标准名（`canonical`）是系统内的权威展示/连接名：通常取中文名（`lang=zh`）；
  无中文译名时取最通行名并标注实际语言（如 `lang=en`）。
- 英文名、母语本名、缩写、旧称一律登记为别名（`aliases[].lang` 标注语言）。
- 解析跨语言：任何语言的名字都映射到同一标准名；`lang` 标签供展示与过滤。

### 2. 存储：文本为事实源，内存为查询层

- **事实源**：`EntityAlias/data/<type>.jsonl`，一行一个实体，按标准名排序，
  原子重写（tmp + replace）——git diff 最小、可直接人工编辑、随仓库版本化。
- **查询层**：启动时全量加载到内存索引（精确 dict + 模糊扫描），千级规模下
  模糊全表扫描也是毫秒级；写操作 = 内存更新 + 整文件重写。
- **向量数据**：完全放在 VectorDB 服务（独立集合），不落文本、不入 git。

## 数据格式（JSONL 每行）

```json
{
  "id": "ent_5f2a1b9c8d3e",
  "canonical": "人民日报",
  "lang": "zh",
  "type": "organization",
  "aliases": [{"name": "People's Daily", "lang": "en"}],
  "created_at": "...", "updated_at": "..."
}
```

约束：一个（归一化后的）名字全库只能属于一个实体；注册冲突返回 409。

## 挂载到 IIS

```python
from EntityAlias.iis_integration import mount_to_iis
mount_to_iis(app, config=config, wrapper=access_manager.login_required,
             vector_client=vector_db_client_or_none)
```

配置（`config.json`）：

```json
{
  "entity_alias": {
    "enabled": true,
    "data_dir": "EntityAlias/data",
    "url_prefix": "/entity-alias",
    "vector_enabled": true
  }
}
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
| POST | `/entity-alias/api/rebuild_vector` | 全量重建向量索引（登录） |
| GET | `/entity-alias/api/status` | 状态 |

AI 调用方的工作流程见 `skill/SKILL.md`。
