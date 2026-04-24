### 一、 `public_storylines` 应该如何整合进系统？（数据隔离与出版机制）

不要直接把前端暴露给你的内部推演表。内部表（`intelligence_storylines`）包含了评分低的失败推演、AI的原始评价（可能带有内部prompt的痕迹）、以及未经脱敏的情报。

我们需要建立一个**ETL（抽取、转换、加载）管道**，将高分的内部推演“出版”为大众简报。

**1. 增加一个“出版 (Publishing)”阶段**
在你的 `DynamicGraphEngine._evaluate_graph_by_ai` 方法末尾，增加落库转换逻辑：

```python
# 假设大模型打分阶段结束
if score >= 8:  # 只有8分以上的高连贯性推演才会被公开
    final_status = "COMPLETED"
    
    # --- 核心：触发出版逻辑 ---
    self._publish_to_public(thread_id, sorted_nodes, edges_list, ai_data)
else:
    final_status = "REJECTED" # 分数低，留在内部库，不公开
```

**2. 转换并剥离敏感/冗余数据**
在 `_publish_to_public` 方法中，你需要将数据重组为适合 C 端阅读的结构，存入独立的 `public_storylines` 集合：

```python
def _publish_to_public(self, thread_id: str, nodes: List[GraphNode], edges: List[GraphEdge], ai_data: dict):
    # 1. 提炼大众友好的元数据
    # 可以让 AI 在总结时顺便生成一个吸睛的标题和标签
    public_title = ai_data.get("public_title", nodes[0].title) 
    
    public_doc = {
        "publish_id": f"PUB_{uuid.uuid4().hex[:8]}",
        "original_thread_id": thread_id,
        "publish_time": time.time(),
        "topic_title": public_title,
        "ai_summary": ai_data.get("llm_summary"),
        # 剥离内部属性：只保留前端渲染需要的 uuid, title, time, brief, key_actors
        "nodes": [ { "id": n.uuid, "title": n.title, "date": n.incident_time, "brief": n.brief, "actors": n.key_actors[:3] } for n in nodes ],
        "edges": [ { "source": e.source_uuid, "target": e.target_uuid, "reason": e.connection_reason } for e in edges ],
        "views": 0,       # 面向大众的业务字段
        "likes": 0
    }
    
    # 2. 存入独立集合
    self.db.db['public_storylines'].insert_one(public_doc)
    logger.info(f"Successfully published thread {thread_id} to public.")
```

---

### 二、 如何保证“推演的连续性”与解决“显示中心偏移”？

这是将“散点工具”升级为“持续订阅产品”的关键。每次聚类产生的种子都不一样，如果每天给大众看一张全新的、毫无关联的图谱，用户会缺乏上下文（即你说的“显示中心都不一样”）。

为了保证连续性，我们需要引入**“主题档案 (Topic Dossier)”**和**“图谱锚定 (Graph Anchoring)”**的概念。

#### 1. 逻辑层：将单次推演合并为“长线主题 (Topic)”
当定时任务选出一个新的种子情报准备推演时，**不要急于开新坑**。
* 先将这个种子的文本（或向量）去和 `public_storylines` 中**已经发布的历史简报**进行相似度检索。
* **如果是旧主题的延续**：系统判定这属于“XX制裁事件（第二季）”，将新的推演节点合并（Merge）到旧的主题树上，更新该主题的最新动态。
* **如果是全新热点**：系统判定这是一个新事件，建立“全新主题（第一季）”。

大众看到的界面不再是“一篇篇文章”，而是**“当前追踪的 5 个大脉络”**，点进去是该脉络在时间轴上的不断生长。

#### 2. 视觉层：解决“显示中心”乱跳的问题（ECharts 布局固定）
你目前使用的是 ECharts 的 `force`（力导向）布局，这种布局每次渲染时，节点初始位置是随机的，导致每次看同一组数据（或略微增加节点的数据）时，图谱形状大变，视觉连续性被破坏。

**技术对策：从“完全随机的力导向”改为“带坐标记忆的演进图”**

* **对策 A：锁定历史节点坐标 (Pinning)**
    当新节点加入图谱时，前端缓存（或后端记录）老节点的 `x` 和 `y` 坐标。在传给 ECharts 的 `nodes` 配置中，将老节点的 `fixed: true`，或者直接赋予确定的 `x, y` 坐标。这样，当引入新情报时，老图谱“岿然不动”，只有新节点像藤蔓一样长出来，视觉中心绝对稳定。

* **对策 B：改变布局策略 (Timeline / Tree / Dagre)**
    面向大众的信息呈现，力导向图往往过于杂乱。既然你的数据有极强的**时间属性 (incident_time)**，强烈建议将图谱横向按照时间轴展开。
    * 在 ECharts 中，可以将节点的 `x` 坐标严格映射为其发生时间的时间戳。
    * `y` 坐标映射为不同的实体分类或关联度。
    * 这样，不管图谱怎么演进，**时间线总是从左到右延伸的**，用户一目了然：“哦，最右边的是今天新长出来的节点”，显示中心自然就跟随时间轴平滑向右推移了。

### 总结下一步行动建议：

1.  **数据库层**：新建 `public_storylines` 集合，并在大模型打分通过后，清洗数据并写入该集合。
2.  **聚类/调度层**：在选出种子后，增加一步与历史发布的 `topic` 进行比对的逻辑。属于同一事件链的，共用一个 `topic_id`。
3.  **前端展示层**：改造目前的工作台页面，为大众打造一个“情报追剧”页面。按 `topic_id` 聚合展示，并在 ECharts 图表中尝试固定 `x` 坐标为时间轴，彻底解决中心飘忽不定的问题。