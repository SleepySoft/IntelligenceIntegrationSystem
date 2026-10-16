# Entity Alias — AI 调用指南

> 目标读者：通过 HTTP API 管理实体别名的 AI Agent。
> 服务基地址：`{BASE}` = IIS 地址 + `/entity-alias`（默认 `http://localhost:5000/entity-alias`）。

## 认证

写操作（POST/PUT/DELETE）需要 IIS 登录会话：

```bash
curl -c cookies.txt -X POST {IIS}/login -d "username=<user>&password=<pass>"
# 之后所有请求带 -b cookies.txt
```

读操作（resolve / lookup / entities 列表）在当前配置下公开可用。

## 核心概念

- 每个实体有且仅有一个**标准名**（canonical，带 `lang` 标签），可有任意多个**别名**（各带 `lang`）
- 归一化（大小写/全半角/标点/空白不敏感）后的名字全库唯一，冲突返回 409
- 语言标签约定：标准名取系统权威展示名（通常 `zh`）；英文名、母语本名作为别名（`en` / 实际语言）

## 场景 1：解析一个名字

```bash
curl "{BASE}/api/resolve?name=People%27s%20Daily"
# => {"name": "People's Daily", "found": true, "entity_id": "ent_...",
#     "canonical": "人民日报", "lang": "zh", "type": "organization",
#     "matched_name": "People's Daily", "match": "exact"}
```

`found=false` 表示未注册。**不要**在未注册时编造标准名——转入场景 2。

## 场景 2：注册新实体（先查相似，再决定）

处理外部输入的未注册实体名列表时，标准流程：

### 步骤 1：批量查询候选

```bash
curl -X POST "{BASE}/api/lookup_batch" -H "Content-Type: application/json" -d '{
  "names": ["纽约时报", "New York Times", "NYT"],
  "limit": 5
}'
```

返回每个名字的 `found` / `exact` / `candidates[]`（含 `canonical`、`score`、`source`：
`partial` 包含匹配 / `fuzzy` 编辑距离 / `vector` 语义召回）。

### 步骤 2：AI 判定

对每个名字判断（这是你的职责，服务不做自动合并）：

- `found=true` → 直接用返回的 `canonical`，无需注册
- candidates 中有明确同一实体（如高分 vector/partial 命中）→ 把新名字**加为已有实体的别名**：
  ```bash
  curl -b cookies.txt -X POST "{BASE}/api/entities/{entity_id}/aliases" \
    -H "Content-Type: application/json" -d '{"name": "NYT", "lang": "en"}'
  ```
- 确认是新实体 → 注册：
  ```bash
  curl -b cookies.txt -X POST "{BASE}/api/entities" -H "Content-Type: application/json" -d '{
    "canonical": "纽约时报",
    "lang": "zh",
    "type": "organization",
    "aliases": [{"name": "The New York Times", "lang": "en"}, {"name": "NYT", "lang": "en"}]
  }'
  ```
  成功返回 201 + 完整实体 JSON（含分配的 `id`）。

### 步骤 3：冲突处理

- 409 `conflict`：名字已被其它实体占用 → 用 `GET /api/entities/{entity_id}` 查看占用方，
  决定是加别名、合并实体，还是换名字
- 404 `not_found`：实体 id 不存在
- 400 `bad_request`：参数问题（空名、重复别名等）

## 场景 3：日常维护

```bash
# 列表（可按类型/关键词过滤）
curl "{BASE}/api/entities?type=organization&q=人民&limit=50"

# 改标准名 / 类型 / 别名数组（整体替换）
curl -b cookies.txt -X PUT "{BASE}/api/entities/{id}" \
  -H "Content-Type: application/json" -d '{"canonical": "新名", "type": "person"}'

# 删别名 / 删实体
curl -b cookies.txt -X DELETE "{BASE}/api/entities/{id}/aliases" \
  -H "Content-Type: application/json" -d '{"name": "旧别名"}'
curl -b cookies.txt -X DELETE "{BASE}/api/entities/{id}"

# 服务状态（实体数、向量是否可用）
curl "{BASE}/api/status"
```

## 注意事项

1. 向量召回（`source=vector`）只是"语义上像"，**不是同一实体的证据**——必须结合上下文人工/AI 确认
2. 注册前永远先 `lookup_batch`：避免同一实体注册两遍
3. 别名 `lang` 用 BCP-47 短码（`zh`/`en`/`ja`/`ru`...）；不确定时留空
4. 批量接口单次建议 ≤ 200 个名字
5. 数据实时写入 git 管理的 JSONL 文本；不要手工编辑数据文件后指望运行中的服务自动感知——重启或等待后续版本的 reload 接口
