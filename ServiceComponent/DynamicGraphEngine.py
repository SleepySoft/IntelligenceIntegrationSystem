import time
import uuid
import logging
import datetime
import threading
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set, Tuple

from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine
from Tools.MongoDBAccess import MongoDBStorage
from AIClientCenter.AIClientManager import BaseAIClient
from MyPythonUtility.DictTools import dict_list_to_markdown
from ServiceComponent.IntelligenceHubDefines_v2 import APPENDIX_TIME_ARCHIVED

logger = logging.getLogger(__name__)


# ==========================================
# Phase 1: Data Structures for Graph Snapshot
# ==========================================

class GraphEdge(BaseModel):
    source_uuid: str
    target_uuid: str
    score: float
    connection_reason: str


class SubNode(BaseModel):
    uuid: str
    title: str
    time: str


class GraphNode(BaseModel):
    uuid: str
    incident_time: Optional[str] = None
    title: str
    brief: str
    is_seed: bool = False
    key_actors: List[str] = []
    location: List[str] = []
    related_docs: List[SubNode] = []


class StorylineSnapshot(BaseModel):
    """单次推演生成的情报脉络图快照"""
    thread_id: str
    seed_uuid: str
    created_at: float = Field(default_factory=time.time)

    nodes: List[GraphNode]
    edges: List[GraphEdge]

    # 异步状态机: QUEUED, PROCESSING, ANALYZING, COMPLETED, REJECTED, FAILED
    status: str = "PROCESSING"

    # LLM 评估字段
    llm_summary: Optional[str] = None
    llm_evaluation_score: Optional[int] = None
    llm_critique: Optional[str] = None

    human_rating: Optional[int] = None


# ==========================================
# Phase 2: The Core Graph Builder Engine
# ==========================================

class DynamicGraphEngine:
    """
    动态情报图谱推演引擎 (基于延迟计算与双向滚雪球算法)
    """

    # --- 软打分公式权重配置 (可根据实际数据表现微调) ---
    WEIGHT_VECTOR = 0.60  # 向量相似度权重 (VectorDB 返回的 score)
    WEIGHT_ENTITY = 0.40  # 罕见实体交集权重
    TIME_DECAY_RATE = 0.008  # 从0.05改为0.008（相差10天扣0.08分，1个月扣0.24分），允许挖掘长线情报
    THRESHOLD_SCORE = 0.55  # 综合得分阈值，低于此分数不建立边

    # --- TF-IDF 实体黑名单 (防止超级节点污染图谱) ---
    # 理想情况下，这应该是一个定时从数据库统计生成的集合，这里提供基础内置名单
    STOP_ENTITIES = {
        "美国", "中国", "俄罗斯", "英国", "法国", "联合国", "欧盟", "北约",
        "US", "CN", "RU", "UK", "FR", "政府", "警方", "军方", "发言人", "分析师"
    }

    def __init__(
            self,
            mongo_db: MongoDBStorage,
            query_engine: Any,
            vector_engine: Any,
            ai_client: Optional[BaseAIClient] = None
    ):
        self.db = mongo_db
        self.query_engine = query_engine
        self.vector_engine = vector_engine
        self.ai_client = ai_client
        self.snapshots_collection = self.db.db['intelligence_storylines']

        # --- 动态高频实体缓存 ---
        self._stop_entities_cache: Set[str] = set()
        self._stop_entities_last_update: float = 0.0
        self._stop_entities_ttl: float = 24 * 3600  # 缓存 24 小时

    @property
    def dynamic_stop_entities(self) -> Set[str]:
        """懒加载 + 定期刷新的高频实体名单"""
        current_time = time.time()
        # 如果缓存为空，或者已经超过 24 小时，重新计算
        if not self._stop_entities_cache or (current_time - self._stop_entities_last_update > self._stop_entities_ttl):
            new_entities = self._refresh_dynamic_stop_entities(days_back=30, top_k=50)
            if new_entities:
                self._stop_entities_cache = new_entities
                self._stop_entities_last_update = current_time

        return self._stop_entities_cache

    # ---------------------------------------------------------
    # Public API: 触发异步推演
    # ---------------------------------------------------------
    def trigger_graph_build(self, seed_uuid: str, max_depth: int = 3, window_days: int = 7) -> str:
        """
        非阻塞接口：触发异步推演图谱任务
        :param seed_uuid: 触发推演的种子情报 UUID
        :param max_depth: BFS 最大搜索深度 (跳数)
        :param window_days: 每次搜索的双向时间窗口 (如 7，表示向前 7 天，向后 7 天)
        :return: 任务标识符 thread_id
        """
        thread_id = f"thread_{uuid.uuid4().hex[:12]}"

        # 1. 初始快照入库
        initial_snapshot = StorylineSnapshot(
            thread_id=thread_id,
            seed_uuid=seed_uuid,
            nodes=[],
            edges=[]
        )
        self.snapshots_collection.insert_one(initial_snapshot.model_dump())

        # 2. 启动后台线程执行计算
        threading.Thread(
            target=self._run_snowballing_pipeline,
            args=(thread_id, seed_uuid, max_depth, window_days),
            daemon=True,
            name=f"GraphBuilder-{thread_id}"
        ).start()

        return thread_id

    def get_snapshot(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """根据 thread_id 获取最新的推演快照状态与数据"""
        snapshot = self.snapshots_collection.find_one({"thread_id": thread_id})
        if snapshot and "_id" in snapshot:
            snapshot["_id"] = str(snapshot["_id"])  # 转换 ObjectId 以便 JSON 序列化
        return snapshot

    # ---------------------------------------------------------
    # Core Pipeline: 在后台线程中执行的具体算法
    # ---------------------------------------------------------

    def _run_snowballing_pipeline(self, thread_id: str, seed_uuid: str, max_depth: int, window_days: int):
        logger.info(f"[{thread_id}] Starting bidirectional graph build for seed: {seed_uuid}")
        try:
            # 1. 提取种子节点
            seed_doc = self.query_engine.get_intelligence(seed_uuid)
            if not seed_doc:
                raise ValueError(f"Seed UUID {seed_uuid} not found in MongoDB.")

            seed_time = self._get_timestamp(seed_doc)

            # 生成专属“时代拦截黑名单”
            era_stop_entities = self._refresh_dynamic_stop_entities(base_time=seed_time, days_back=30)

            nodes_dict: Dict[str, GraphNode] = {}
            edges_list: List[GraphEdge] = []
            visited_uuids = {seed_uuid}

            nodes_dict[seed_uuid] = self._create_graph_node(seed_doc, is_seed=True)
            current_entities_pool = self._extract_rare_entities(seed_doc, era_stop_entities)

            seed_text = self.vector_engine.build_search_text(seed_doc, data_type='summary')

            # 探索队列: (当前UUID, 参考时间, 用于检索的Text, 当前深度)
            exploration_queue = [(seed_uuid, seed_time, seed_text, 0)]

            # --- 算法调控参数 ---
            MAX_BRANCHES_PER_NODE = 2
            ECHO_TIME_WINDOW_SEC = 3 * 24 * 3600
            ECHO_VECTOR_SIM_THRESHOLD = 0.88

            # 2. 开始 N 轮深度迭代 (BFS)
            while exploration_queue:
                # 🚨 防爆破断路器
                if len(nodes_dict) >= 50:
                    logger.warning(f"[{thread_id}] 节点数已达 50 个上限，触发熔断，提前结束搜索。")
                    break

                current_uuid, current_time, current_text, depth = exploration_queue.pop(0)

                if depth >= max_depth:
                    continue

                dt_center = datetime.datetime.fromtimestamp(current_time, tz=datetime.timezone.utc)
                dt_start = dt_center - datetime.timedelta(days=window_days)
                dt_end = dt_center + datetime.timedelta(days=window_days)

                logger.info(
                    f"\n{'=' * 60}\n"
                    f"🔍 [Depth {depth + 1}/{max_depth}] 探索起点: {current_uuid}\n"
                    f"📅 中心时间: {dt_center.strftime('%Y-%m-%d %H:%M')}\n"
                    f"⏳ 逻辑检索窗口: [{dt_start.strftime('%Y-%m-%d')}] TO [{dt_end.strftime('%Y-%m-%d')}]\n"
                    f"🎯 当前追踪实体池: {list(current_entities_pool)[:8]}...\n"
                    f"{'=' * 60}"
                )

                # --- A. VectorDB 宽泛召回 ---
                candidates = self.vector_engine.query(
                    text=current_text,
                    top_n=150,
                    score_threshold=0.50
                )

                # --- B. 批量获取 MongoDB 数据 ---
                cand_uuids = [c.get("doc_id") for c in candidates if
                              c.get("doc_id") and c.get("doc_id") not in visited_uuids]
                if not cand_uuids:
                    continue

                cand_docs_list = self.query_engine.get_intelligence(cand_uuids, light_weight=True)
                cand_docs_dict = {doc['UUID']: doc for doc in cand_docs_list if doc}

                # --- C. 内存精算与多维软打分 ---
                current_round_passed = []

                for cand in candidates:
                    cand_uuid = cand.get("doc_id")
                    if cand_uuid not in cand_uuids:
                        continue

                    cand_doc = cand_docs_dict.get(cand_uuid)
                    if not cand_doc:
                        continue

                    cand_vec_sim = cand.get("score", 0.0)
                    cand_time = self._get_timestamp(cand_doc)
                    if not (dt_start.timestamp() <= cand_time <= dt_end.timestamp()):
                        continue

                    cand_time_str = datetime.datetime.fromtimestamp(cand_time, tz=datetime.timezone.utc).strftime(
                        '%Y-%m-%d')

                    cand_entities = self._extract_rare_entities(cand_doc, era_stop_entities)
                    score, reason = self._calculate_hybrid_score(
                        time_a=current_time, time_b=cand_time, vec_sim=cand_vec_sim,
                        entities_pool=current_entities_pool, cand_entities=cand_entities
                    )

                    if score >= self.THRESHOLD_SCORE:
                        current_round_passed.append(
                            (cand_uuid, cand_doc, cand_time, score, reason, cand_vec_sim, cand_time_str))

                # 排序（按总分降序）
                current_round_passed.sort(key=lambda x: x[3], reverse=True)

                # --- D. 排队安检，逐个入库 ---
                accepted_this_round = 0
                for cand_uuid, cand_doc, cand_time, score, reason, cand_vec_sim, cand_time_str in current_round_passed:
                    if accepted_this_round >= MAX_BRANCHES_PER_NODE:
                        break

                    matched_node = None
                    for existing_node in nodes_dict.values():
                        exist_dt_str = existing_node.incident_time
                        if exist_dt_str:
                            try:
                                # 确保时间格式匹配 _create_graph_node 中生成的格式 (%Y-%m-%d %H:%M)
                                exist_dt = datetime.datetime.strptime(exist_dt_str, "%Y-%m-%d %H:%M").replace(
                                    tzinfo=datetime.timezone.utc)

                                time_diff_sec = abs(cand_time - exist_dt.timestamp())
                                time_diff_days = time_diff_sec / (24 * 3600)

                                # 【核心修复】必须“时间近 AND 语义像”才能折叠！
                                # 规则 A：同一天内发生，且向量相似度较高 (>= 0.65)，认为是同一事件的补充报道
                                # 规则 B：三天内发生，且向量相似度极高 (>= 0.85)，认为是滞后/跟风报道
                                if (time_diff_days <= 1 and cand_vec_sim >= 0.65) or \
                                        (time_diff_days <= 3 and cand_vec_sim >= 0.85):
                                    matched_node = existing_node
                                    break
                            except ValueError as ve:
                                # 增加日志，暴露出时间格式不匹配的问题
                                logger.debug(f"Time parse error for {exist_dt_str}: {ve}")
                                pass

                    if matched_node:
                        # 【防御 1】填补 BFS 漏洞：即使被折叠，也要标记为已访问，防止后续轮次重复召回！
                        visited_uuids.add(cand_uuid)

                        # 【防御 2】UI 级去重保险：防范数据库里本来就存在的重复脏数据
                        existing_uuids = {sub.uuid for sub in matched_node.related_docs}
                        # 截取前15个字符作为标题去重依据，防止微小的末尾差异
                        short_title = cand_doc.get("EVENT_TITLE", "")[:15]
                        existing_titles = {sub.title[:15] for sub in matched_node.related_docs}

                        if cand_uuid not in existing_uuids and short_title not in existing_titles:
                            logger.info(
                                f"  🔕 [ECHO] 发现相似情报，折叠至节点: {matched_node.uuid} (相似度: {cand_vec_sim:.2f})")

                            matched_node.related_docs.append(SubNode(
                                uuid=cand_uuid,
                                title=cand_doc.get("EVENT_TITLE", "")[:20] + "...",
                                time=datetime.datetime.fromtimestamp(cand_time, tz=datetime.timezone.utc).strftime(
                                    '%Y-%m-%d %H:%M')
                            ))
                            # 依然吸收它的罕见实体以滋养图谱
                            current_entities_pool.update(self._extract_rare_entities(cand_doc, era_stop_entities))
                        else:
                            logger.debug(f"[DUPLICATE] 节点 {cand_uuid} 已存在于折叠列表中或标题高度重复，跳过。")

                        continue

                    # 安检通过，真正拉入图谱！
                    title = cand_doc.get("EVENT_TITLE", "")[:15] + "..."
                    logger.info(f"[LINKED] [{cand_time_str}] {title} | 总分: {score:.2f} | 依据: {reason}")

                    visited_uuids.add(cand_uuid)
                    nodes_dict[cand_uuid] = self._create_graph_node(cand_doc)
                    edges_list.append(GraphEdge(
                        source_uuid=current_uuid, target_uuid=cand_uuid, score=score, connection_reason=reason
                    ))

                    next_text = self.vector_engine.build_search_text(cand_doc, data_type='summary')
                    exploration_queue.append((cand_uuid, cand_time, next_text, depth + 1))
                    current_entities_pool.update(self._extract_rare_entities(cand_doc, era_stop_entities))

                    accepted_this_round += 1

                # 实时流式刷入数据库
                # 只要本轮有新节点加入，就刷入一次数据库更新前台状态
                if accepted_this_round > 0:
                    current_nodes = sorted(list(nodes_dict.values()),
                                           key=lambda x: x.incident_time if x.incident_time else "")
                    self._update_snapshot_status(thread_id, current_nodes, edges_list, "PROCESSING")

                # 强制挂起当前线程 50 毫秒，主动交出 Python GIL 锁！
                # 这点时间对总推演时长微乎其微，但能让 Flask Web 线程瞬间复活，从容处理前端的 /status 轮询！
                time.sleep(0.05)

            # 3. 排序与结构化
            sorted_nodes = sorted(list(nodes_dict.values()), key=lambda x: x.incident_time if x.incident_time else "")

            # 4. 图谱构建完成，进入评估阶段
            if self.ai_client and len(sorted_nodes) > 1:
                self._update_snapshot_status(thread_id, sorted_nodes, edges_list, "ANALYZING")
                self._evaluate_graph_by_ai(thread_id, sorted_nodes)
            else:
                self._update_snapshot_status(thread_id, sorted_nodes, edges_list, "COMPLETED")

            logger.info(f"[{thread_id}] Graph build finished. Nodes: {len(sorted_nodes)}, Edges: {len(edges_list)}")

        except Exception as e:
            logger.error(f"[{thread_id}] Graph build failed: {str(e)}", exc_info=True)
            self.snapshots_collection.update_one({"thread_id": thread_id}, {"$set": {"status": "FAILED"}})

    # ---------------------------------------------------------
    # Helpers: 算法打分与特征提取
    # ---------------------------------------------------------

    def _calculate_hybrid_score(self, time_a: float, time_b: float, vec_sim: float,
                                entities_pool: Set[str], cand_entities: Set[str]) -> Tuple[float, str]:
        """执行软打分公式"""
        # 1. 实体交集得分 (交集数量越多，得分越高，上限设为 1.0)
        overlap = entities_pool.intersection(cand_entities)
        ent_score = min(len(overlap) * 0.5, 1.0)  # 找到1个共享实体给0.5分，2个以上给满分1.0

        # 2. 绝对时间差惩罚
        delta_days = abs(time_a - time_b) / (24 * 3600)
        time_penalty = delta_days * self.TIME_DECAY_RATE

        # 3. 综合得分公式
        total_score = (self.WEIGHT_VECTOR * vec_sim) + (self.WEIGHT_ENTITY * ent_score) - time_penalty

        # 构建解释字符串，供前端审查视图展示
        reason_parts = [f"VecSim: {vec_sim:.2f}"]
        if overlap:
            reason_parts.append(f"Shared Entities: {list(overlap)[:3]}")
        if time_penalty > 0:
            reason_parts.append(f"Time Penalty: -{time_penalty:.2f} ({delta_days:.1f} days)")

        return total_score, " | ".join(reason_parts)

    def _extract_rare_entities(self, doc: dict, stop_entities: Set[str]) -> Set[str]:
        """提取实体并过滤动态高频词"""
        entities = set()
        people = doc.get("PEOPLE") or []
        orgs = doc.get("ORGANIZATION") or []
        locs = doc.get("LOCATION") or []

        for e in (people + orgs + locs):
            if isinstance(e, str) and e.strip() and e not in stop_entities:
                entities.add(e)
        return entities

    def _get_timestamp(self, doc: dict) -> float:
        appendix = doc.get("APPENDIX") or {}
        # 注意：这里直接使用你指定的字段名，或者使用你定义的 APPENDIX_TIME_ARCHIVED 变量
        arch_time = appendix.get("__TIME_ARCHIVED__")

        if arch_time is None:
            logger.warning(f"[Time Missing] UUID {doc.get('UUID', 'Unknown')} 缺失归档时间，使用当前时间兜底！")
            return time.time()

        if isinstance(arch_time, datetime.datetime):
            # MongoDB 默认存的是 UTC，但 PyMongo 返回的可能是 naive (无时区) 的 datetime
            # 我们需要强制附加上 UTC 时区，再转换为时间戳，防止服务器本地时区污染
            if arch_time.tzinfo is None:
                arch_time = arch_time.replace(tzinfo=datetime.timezone.utc)
            return arch_time.timestamp()

        if isinstance(arch_time, str):
            try:
                return datetime.datetime.fromisoformat(arch_time.replace('Z', '+00:00')).timestamp()
            except ValueError:
                logger.warning(f"[Time Parse Failed] UUID {doc.get('UUID')} 的归档时间格式异常: {arch_time}")
                pass

        logger.warning(f"[Time Missing] UUID {doc.get('UUID')} 缺失归档时间，使用当前系统时间兜底！")

        return time.time()  # 兜底策略

    def _create_graph_node(self, doc: dict, is_seed: bool = False) -> GraphNode:
        timestamp = self._get_timestamp(doc)
        dt_str = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')

        return GraphNode(
            uuid=doc.get("UUID", "Unknown"),
            incident_time=dt_str,  # 统一使用格式化后的归档时间
            title=doc.get("EVENT_TITLE") or "无标题",
            brief=doc.get("EVENT_BRIEF") or "",
            is_seed=is_seed,
            key_actors=(doc.get("PEOPLE") or []) + (doc.get("ORGANIZATION") or []),
            location=doc.get("LOCATION") or []
        )

    def _update_snapshot_status(self, thread_id: str, nodes: List[GraphNode], edges: List[GraphEdge], status: str):
        self.snapshots_collection.update_one(
            {"thread_id": thread_id},
            {"$set": {
                "nodes": [n.model_dump() for n in nodes],
                "edges": [e.model_dump() for e in edges],
                "status": status
            }}
        )

    # ---------------------------------------------------------
    # AI Evaluation (人机协同闭环)
    # ---------------------------------------------------------
    def _evaluate_graph_by_ai(self, thread_id: str, sorted_nodes: List[GraphNode]):
        """
        调用 LLM 对生成的脉络图谱进行逻辑连贯性审查与总结。
        """
        logger.info(f"[{thread_id}] Requesting AI evaluation for graph...")

        # 构建用于喂给大模型的简要时间线 Markdown
        timeline_data = []
        for n in sorted_nodes:
            timeline_data.append({
                "Date": n.incident_time or "Unknown",
                "Event": f"【{n.title}】 {n.brief}"
            })

        timeline_md = dict_list_to_markdown(timeline_data)

        prompt = """你是一个高级情报审查官。以下是系统通过算法自动抓取的按时间排序的事件脉络。
请审查：1. 这些事件是否真的构成一个连贯的故事？ 2. 有没有因为人名/地名重名而被错误关联进来的“杂音情报”？

请严格输出以下 JSON 结构：
{
    "llm_evaluation_score": 整数(1-10)，评估这条脉络的整体逻辑连贯性。如果有明显的错误关联，请给低于5分。
    "llm_critique": "你的审查意见，比如指出哪一天的事情可能是杂音，或者赞扬其连贯性。",
    "llm_summary": "请基于这份时间线，撰写一份包含‘历史起因 - 核心爆发 - 后续演变’的200字结构化态势总结。"
}"""

        user_message = f"## 事件脉络时间线\n{timeline_md}"
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message}
        ]

        try:
            # 复用你现有的 AIClient 逻辑
            response = self.ai_client.chat(messages=messages, temperature=0.1)

            # # 这里简单提取 JSON（生产中可引入你的 parse_ai_response 方法）
            # from AIClientCenter.AIClientManager import extract_pure_response
            # from MyPythonUtility.AIUtil import extract_pure_json_text  # 假设你有这个工具

            ai_output = response["choices"][0]["message"]["content"]
            import json
            # 清理 Markdown 代码块
            ai_json_str = ai_output.strip().removeprefix('```json').removesuffix('```').strip()
            ai_data = json.loads(ai_json_str)

            # 判决逻辑
            score = ai_data.get("llm_evaluation_score", 5)
            final_status = "COMPLETED" if score >= 5 else "REJECTED"

            self.snapshots_collection.update_one(
                {"thread_id": thread_id},
                {"$set": {
                    "llm_evaluation_score": score,
                    "llm_critique": ai_data.get("llm_critique"),
                    "llm_summary": ai_data.get("llm_summary"),
                    "status": final_status
                }}
            )
            logger.info(f"[{thread_id}] AI Evaluation done. Score: {score}, Status: {final_status}")

        except Exception as e:
            logger.warning(f"[{thread_id}] AI Evaluation failed: {e}. Defaulting status to COMPLETED.")
            self.snapshots_collection.update_one(
                {"thread_id": thread_id},
                {"$set": {"status": "COMPLETED"}}
            )

    def _refresh_dynamic_stop_entities(self, base_time: float = None, days_back: int = 30, top_k: int = 50) -> Set[str]:
        logger.info(f"Refreshing dynamic STOP_ENTITIES from MongoDB (past {days_back} days, top {top_k})...")

        # 1. 计算时间窗口的起点
        if base_time:
            # 基于种子情报发生的时间往前推 30 天
            end_date = datetime.datetime.fromtimestamp(base_time, tz=datetime.timezone.utc)
        else:
            end_date = datetime.datetime.now(datetime.timezone.utc)

        cutoff_date = end_date - datetime.timedelta(days=days_back)

        pipeline = [
            {
                "$match": {
                    # 匹配发生在那个时间段内的数据
                    f"APPENDIX.{APPENDIX_TIME_ARCHIVED}": {"$gte": cutoff_date, "$lte": end_date}
                }
            },
            # Step 2: 将三个实体数组合并成一个统一的数组池
            {
                "$project": {
                    "all_entities": {
                        "$concatArrays": [
                            {"$ifNull": ["$PEOPLE", []]},
                            {"$ifNull": ["$ORGANIZATION", []]},
                            {"$ifNull": ["$LOCATION", []]}
                        ]
                    }
                }
            },
            # Step 3: 将数组打散为一条条独立的记录 (Unwind)
            {
                "$unwind": "$all_entities"
            },
            # Step 4: 清洗空字符串或无效格式
            {
                "$match": {
                    "all_entities": {"$type": "string", "$ne": "", "$regex": "^\\s*\\S+"}
                }
            },
            # Step 5: 按实体名称分组，统计出现次数
            {
                "$group": {
                    "_id": "$all_entities",
                    "count": {"$sum": 1}
                }
            },
            # Step 6: 按出现频率降序排列，取 Top K
            {
                "$sort": {"count": -1}
            },
            {
                "$limit": top_k
            }
        ]

        try:
            # 调用你封装好的 MongoDBStorage.aggregate 方法
            results = self.db.aggregate(pipeline)

            dynamic_stop_entities = set()
            for row in results:
                entity = row.get("_id", "").strip()
                if entity:
                    dynamic_stop_entities.add(entity)

            logger.info(
                f"Discovered {len(dynamic_stop_entities)} dynamic stop entities: {list(dynamic_stop_entities)[:10]}...")
            return dynamic_stop_entities

        except Exception as e:
            logger.error(f"Failed to calculate dynamic stop entities: {e}", exc_info=True)
            # 失败时返回保守的内置默认名单，避免引擎瘫痪
            return DynamicGraphEngine.STOP_ENTITIES
