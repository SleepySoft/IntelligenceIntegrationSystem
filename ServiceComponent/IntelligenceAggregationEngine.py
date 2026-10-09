# ServiceComponent/IntelligenceAggregationEngine.py

from __future__ import annotations

import os
import time
import logging
import datetime
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

from GlobalConfig import EXPORT_PATH
from VectorDB.VectorDBClient import VectorDBClient

logger = logging.getLogger(__name__)


@dataclass
class AggregationPlanSpec:
    plan_id: str
    collection_name: str
    time_window_sec: int = 24 * 3600
    run_every_sec: int = 3600
    filter_criteria: Dict[str, Any] = None
    limit: int = 50000
    max_points: int = 50000
    method: str = "hdbscan"
    params: Dict[str, Any] = None
    semantic_only: bool = True
    enable_online: bool = True
    online_params: Dict[str, Any] = None
    persist: bool = True
    time_field: str = "archived_timestamp"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "collection_name": self.collection_name,
            "time_window_sec": self.time_window_sec,
            "run_every_sec": self.run_every_sec,
            "filter_criteria": self.filter_criteria or {},
            "limit": int(self.limit),
            "max_points": int(self.max_points),
            "method": self.method,
            "params": self.params or {},
            "semantic_only": bool(self.semantic_only),
            "enable_online": bool(self.enable_online),
            "online_params": self.online_params or {},
            "persist": bool(self.persist),
            "time_field": self.time_field,
        }


def generate_aggregation_plan(profile: str = "hdbscan_fine") -> AggregationPlanSpec:
    """
    Generates an AggregationPlanSpec based on predefined algorithm profiles.
    This allows easy switching between clustering strategies for testing and tuning.

    Available profiles:
    - "hdbscan_balanced": Default density-based clustering, favors stable macro-clusters.
    - "hdbscan_fine": Forces extraction of micro-clusters (leaves) for finer granularity.
    - "agglomerative_strict": Hard cut-off distance, strict similarity grouping.
    - "agglomerative_loose": Hard cut-off distance, looser grouping.
    - "dbscan_standard": Classic DBSCAN, simple density grouping.
    """
    # Base configuration shared across all profiles
    base_config = {
        "plan_id": "agg_intelligence_summary_24h",
        "collection_name": "intelligence_summary",
        "time_window_sec": 24 * 3600,
        "run_every_sec": 3600,
        "max_points": 50000,
        "enable_online": True,
        "online_params": {"T_event": 0.85, "T_dup": 0.95},
        "persist": True,
        "time_field": "archived_timestamp",
    }

    if profile == "hdbscan_balanced":
        return AggregationPlanSpec(
            **base_config,
            method="hdbscan",
            params={
                "min_cluster_size": 3,
                "min_samples": 2,
                "cluster_selection_method": "eom"
            }
        )

    elif profile == "hdbscan_fine":
        return AggregationPlanSpec(
            **base_config,
            method="hdbscan",
            params={
                "min_cluster_size": 2,  # Allow very small clusters
                "min_samples": 1,  # Less conservative core point definition
                "cluster_selection_method": "leaf",  # Force fine-grained micro-clusters
                "cluster_selection_epsilon": 0.0  # Do not merge based on distance
            }
        )

    elif profile == "agglomerative_strict":
        return AggregationPlanSpec(
            **base_config,
            method="agglomerative_threshold",
            params={
                "distance_threshold": 0.25,  # Max cosine distance to be in same cluster
                "metric": "cosine",
                "linkage": "average"
            }
        )

    elif profile == "agglomerative_loose":
        return AggregationPlanSpec(
            **base_config,
            method="agglomerative_threshold",
            params={
                "distance_threshold": 0.45,  # Allows more variance within the cluster
                "metric": "cosine",
                "linkage": "average"
            }
        )

    elif profile == "dbscan_standard":
        return AggregationPlanSpec(
            **base_config,
            method="dbscan",
            params={
                "eps": 0.25,  # Maximum distance between two samples
                "min_samples": 2,
                "metric": "cosine"
            }
        )

    else:
        raise ValueError(f"Unknown aggregation profile: {profile}")


class IntelligenceAggregationEngine:
    """
    IIS-side wrapper for VectorDB aggregation APIs.
    - Ensure plan exists
    - Trigger offline runs (hourly by IIS scheduler)
    - Read latest offline / online state
    - (Placeholder) Appendix writeback hook (NOT implemented now)
    """

    def __init__(
        self,
        vector_client: VectorDBClient,
        plan_spec: AggregationPlanSpec,
    ):
        self.client = vector_client
        self.plan_spec = plan_spec

        self.last_job_id: Optional[str] = None
        self.last_trigger_at: float = 0.0

    # ----------------------------
    # Plan management
    # ----------------------------

    def ensure_plan(self, overwrite: bool = False) -> Dict[str, Any]:
        """
        Ensure the plan exists on VectorDBService.
        """
        payload = self.plan_spec.to_payload()
        return self.client.register_aggregation_plan(payload, overwrite=overwrite)

    def list_plans(self) -> Dict[str, Any]:
        return self.client.list_aggregation_plans()

    # ----------------------------
    # Offline run
    # ----------------------------

    def trigger_offline(self, overrides: Optional[Dict[str, Any]] = None,
                        time_range: Optional[Tuple[float, float]] = None,
                        doc_fetcher: callable = None) -> str:
        """
        Trigger offline aggregation and return job_id.
        同时启动后台线程监控任务，并在完成时生成 Snapshot 文件。
        """
        res = self.client.run_aggregation_plan(self.plan_spec.plan_id, overrides=overrides, time_range=time_range)
        job_id = res.get("job_id")
        self.last_job_id = job_id
        self.last_trigger_at = time.time()

        if job_id:
            threading.Thread(
                target=self._monitor_offline_job,
                args=(job_id, doc_fetcher),
                daemon=True
            ).start()

        return job_id

    def _monitor_offline_job(self, job_id: str, doc_fetcher: callable):
        """
        临时线程：轮询作业状态并生成文本分析报告
        """
        logger.info(f"Started monitoring offline aggregation job: {job_id}")
        while True:
            try:
                job_info = self.get_job(job_id)
                status = job_info.get("status")

                if status == "completed":
                    logger.info(f"Job {job_id} completed. Generating text snapshot...")
                    self._generate_snapshot(job_id, doc_fetcher)
                    break
                elif status == "failed":
                    logger.error(f"Job {job_id} failed. Reason: {job_info.get('error')}")
                    break

                time.sleep(5)  # 每5秒轮询一次
            except Exception as e:
                logger.error(f"Error monitoring job {job_id}: {str(e)}")
                time.sleep(5)

    def _generate_snapshot(self, job_id: str, doc_fetcher: callable):
        try:
            latest = self.get_latest_offline()
            if not latest:
                return

            clusters = latest.get("clusters", {})

            export_folder = os.path.join(EXPORT_PATH, 'clusters')
            os.makedirs(export_folder, exist_ok=True)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"offline_cluster_snapshot_{timestamp}.txt"
            filepath = os.path.join(export_folder, filename)

            # 收集并获取所需的文章标题以便调试阅读
            all_uuids = set()
            for cid, cinfo in clusters.items():
                if cinfo.get("repr_doc_id"):
                    all_uuids.add(cinfo["repr_doc_id"])
                for member_id in (cinfo.get("members") or []):
                    all_uuids.add(member_id)

            uuid_to_title = {}
            if doc_fetcher and all_uuids:
                docs = doc_fetcher(list(all_uuids))
                if isinstance(docs, dict): docs = [docs]
                for d in docs:
                    if not isinstance(d, dict): continue
                    uid = self._document_id(d)
                    title = self._message(d).get("title") or "(No Title)"
                    if uid:
                        uuid_to_title[uid] = title

            # 写入快照
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"=== Offline Clustering Snapshot ===\n")
                f.write(f"Job ID:      {job_id}\n")
                f.write(f"Plan ID:     {latest.get('plan_id')}\n")
                f.write(f"Generated:   {datetime.datetime.now().isoformat()}\n")
                f.write(f"Total Points:{latest.get('n_points', 0)}\n")
                f.write(f"Clusters:    {latest.get('n_clusters', 0)}\n")
                f.write(f"Noise Size:  {latest.get('n_noise', 0)}\n")
                f.write("===================================\n\n")

                sorted_clusters = sorted(clusters.items(), key=lambda x: x[1].get("size", 0), reverse=True)

                for cid, cinfo in sorted_clusters:
                    size = cinfo.get("size", 0)
                    repr_id = cinfo.get("repr_doc_id")
                    repr_title = uuid_to_title.get(repr_id, "Unknown Title / Database Missing")

                    f.write(f"[{cid}] Size: {size}\n")
                    f.write(f"  ★ Representative:\n")
                    f.write(f"     -> {repr_title}\n")
                    # f.write(f"     -> {repr_id} | {repr_title}\n")
                    f.write(f"  ● Members:\n")

                    for mid in cinfo.get("members", []):
                        m_title = uuid_to_title.get(mid, "Unknown Title")
                        f.write(f"     - {m_title}\n")
                        # f.write(f"     - {mid} | {m_title}\n")

                    f.write("\n")

            logger.info(f"Snapshot successfully written to {filepath}")

        except Exception as e:
            logger.error(f"Failed to generate snapshot for {job_id}: {str(e)}")

    # ----------------------------
    # High-level API Abstractions
    # ----------------------------

    def build_rich_clusters_latest(
            self,
            doc_fetcher: callable,
            doc_cleaner: callable,
            limit: int = 200,
            descending: bool = True,
            source: str = "offline"
    ) -> Dict[str, Any]:
        """
        组合业务逻辑：获取簇的摘要。
        外层列表永远按代表性文档的 ``total_score`` 排列。
        """
        # 注意：这里 limit=0 表示拉取全部簇，或者你可以设一个较大的安全值(比如1000)，
        # 绝对不能在这里用 200 截断，否则高分但体积小的簇会被提前丢弃！
        summary = self.get_clusters_summary(
            source=source, sort_by="size", descending=True, limit=0, include_noise=False
        )
        clusters = summary.get("clusters") or []
        if not clusters:
            return summary

        state = self.get_cluster_state(source=source) or {}
        raw_clusters = state.get("clusters") or {}
        cluster_members: Dict[str, List[str]] = {}
        fetch_ids = []

        for c in clusters:
            cid = c.get("cluster_id")
            raw_cluster = raw_clusters.get(cid) or {}
            members = list(raw_cluster.get("members") or [])
            if not members and c.get("repr_doc_id"):
                members = [c.get("repr_doc_id")]
            cluster_members[cid] = members
            fetch_ids.extend(members)

        fetch_ids = list(dict.fromkeys([x for x in fetch_ids if x]))
        fetched_docs = doc_fetcher(fetch_ids) if fetch_ids else []
        if isinstance(fetched_docs, dict):
            fetched_docs = [fetched_docs]
        doc_map = {self._document_id(d): d for d in (fetched_docs or []) if isinstance(d, dict)}

        out_clusters = []
        for c in clusters:
            cid = c.get("cluster_id")
            member_docs = [doc_map[mid] for mid in cluster_members.get(cid, []) if mid in doc_map]
            doc = max(member_docs, key=self._get_archived_sort_value) if member_docs else {}
            uuid = self._document_id(doc) if doc else c.get("repr_doc_id")
            message = self._message(doc)
            title = message.get("title") or "(No Title)"
            brief = message.get("brief") or ""

            cleaned_docs = doc_cleaner([doc]) if doc else []
            cleaned_doc = cleaned_docs[0] if cleaned_docs else {}

            out_clusters.append({
                "cluster_id": c.get("cluster_id"),
                "size": c.get("size", 0),
                "last_seen": c.get("last_seen"),
                "repr_uuid": uuid,
                "cluster_repr_uuid": c.get("repr_doc_id"),
                "repr_strategy": "latest_archived",
                "repr_title": title,
                "repr_brief": brief,
                "href": f"/intelligence/{uuid}" if uuid else "#",
                "repr_doc": cleaned_doc
            })

        # 外层强制执行：永远按评分排序
        out_clusters.sort(
            key=lambda x: float(x.get("repr_doc", {}).get("total_score", 0.0) or 0.0),
            reverse=descending
        )

        # 排序完成之后，再根据用户请求的 limit 进行分页/截断
        if limit and limit > 0:
            out_clusters = out_clusters[:limit]

        summary["clusters"] = out_clusters
        return summary

    def _get_archived_sort_value(self, doc: Dict[str, Any]) -> float:
        raw = doc.get("archived_at")
        if raw is None:
            return 0.0
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, datetime.datetime):
            return raw.timestamp()
        if isinstance(raw, str):
            try:
                return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
        return 0.0

    def build_rich_cluster_members(
            self,
            cluster_id: str,
            doc_fetcher: callable,
            doc_cleaner: callable,
            offset: int = 0,
            limit: int = 100,
            sort_by: str = "relevance",  # 支持 "relevance", "score", "time"
            descending: bool = True,
            source: str = "offline"
    ) -> Dict[str, Any]:
        """
        组合业务逻辑：获取簇内成员切片。
        内层支持按 relevance(默认簇内顺序)、score(文章评分) 或 time(归档时间) 排序。
        """
        state = self.get_cluster_state(source=source) or {}
        clusters = state.get("clusters") or {}
        cobj = clusters.get(cluster_id)
        if not cobj:
            raise ValueError(f"cluster_id not found: {cluster_id}")

        members = cobj.get("members") or []
        total = len(members)

        offset = max(0, offset)
        limit = max(1, min(500, limit))

        # 关键分流：如果按相关度排，直接信任 HDBSCAN 生成的 members 顺序，提前切片省内存；
        # 如果按分数或时间排，必须拿到所有文档的具体字段后才能排序，所以要传全量 members。
        if sort_by in ("score", "time"):
            fetch_members = members
        else:
            fetch_members = members[offset: offset + limit]

        docs = doc_fetcher(fetch_members) if fetch_members else []
        if isinstance(docs, dict):
            docs = [docs]

        # 记录原始的“簇心相关度”排名索引，用于 relevance 排序恢复顺序
        rank = {u: i for i, u in enumerate(fetch_members)}

        cleaned_docs = doc_cleaner([d for d in docs if isinstance(d, dict)])
        cleaned_map = {self._document_id(d): d for d in cleaned_docs}

        items = []
        for uid in fetch_members:
            if uid not in rank:
                continue
            cleaned_doc = cleaned_map.get(uid, {})
            title = self._message(cleaned_doc).get("title") or "(No Title)"

            items.append({
                "uuid": uid,
                "title": title,
                "href": f"/intelligence/{uid}",
                "doc": cleaned_doc
            })

        # 针对全量拿到的 items 重新排序
        if sort_by == "score":
            items.sort(
                key=lambda x: float(x["doc"].get("total_score", 0.0) or 0.0),
                reverse=descending
            )
        elif sort_by == "time":
            items.sort(
                key=lambda x: self._get_archived_sort_value(x["doc"]),
                reverse=descending
            )
        else:
            # relevance (Default) 默认排序，按原本在 members 里的距离/排名
            items.sort(key=lambda x: rank.get(x["uuid"], 10 ** 9))

        # 如果是全量获取后重新排序的，排序完在这里执行切片
        if sort_by in ("score", "time"):
            items = items[offset: offset + limit]

        return {
            "cluster_id": cluster_id,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": items
        }

    @staticmethod
    def _document_id(doc: Dict[str, Any]) -> str:
        return str(doc.get("intelligence_uuid") or doc.get("_id") or "")

    @staticmethod
    def _message(doc: Dict[str, Any]) -> Dict[str, Any]:
        return (doc.get("analysis") or {}).get("message") or {}

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self.client.get_aggregation_job(job_id)

    # ----------------------------
    # Read states
    # ----------------------------

    def get_latest_offline(self) -> Dict[str, Any]:
        return self.client.get_aggregation_offline_latest(self.plan_spec.plan_id)

    def get_online_state(self) -> Dict[str, Any]:
        return self.client.get_aggregation_online_state(self.plan_spec.plan_id)

    def get_cluster_items(self, cluster_id: str, limit: int = 100) -> Dict[str, Any]:
        return self.client.get_aggregation_offline_cluster_items(self.plan_spec.plan_id, cluster_id, limit=limit)

    def find_cluster_of_doc(self, doc_id: str, prefer_online: bool = True) -> Optional[str]:
        """
        Return cluster_id for given doc_id (uuid). Online preferred.
        """
        if prefer_online:
            st = self.get_online_state() or {}
            m = st.get("doc_to_cluster") or {}
            if doc_id in m:
                return m[doc_id]

        off = self.get_latest_offline() or {}
        m2 = off.get("doc_to_cluster") or {}
        return m2.get(doc_id)

    def get_cluster_state(self, source: str = "offline") -> Dict[str, Any]:
        """
        Return raw aggregation state.

        "online" is the runtime state initialized from latest offline and then
        incrementally updated by VectorDB upsert events. It falls back to
        offline when the online state is unavailable.
        """
        source = (source or "offline").lower().strip()
        if source == "online":
            online = self.get_online_state() or {}
            if "clusters" in online:
                return online
        return self.get_latest_offline() or {}

    def get_clusters_summary(
            self,
            *,
            source: str = "offline",
            sort_by: str = "size",  # "size" | "last_seen" | "cluster_id"
            descending: bool = True,
            limit: int = 200,
            include_noise: bool = True,
    ) -> Dict[str, Any]:
        """
        Return a compact cluster summary from online runtime state or latest
        offline result.
        """
        requested_source = (source or "offline").lower().strip()
        state = self.get_cluster_state(source=requested_source) or {}
        actual_source = "online" if requested_source == "online" and "base_version" in state else "offline"

        if not state or (state.get("version") is None and state.get("base_version") is None):
            return {
                "plan_id": self.plan_spec.plan_id,
                "collection_name": self.plan_spec.collection_name,
                "version": None,
                "base_version": None,
                "source": requested_source,
                "actual_source": actual_source,
                "created_at": None,
                "updated_at": state.get("updated_at"),
                "time_range": None,
                "method": None,
                "params": None,
                "n_points": 0,
                "n_clusters": 0,
                "n_noise": 0,
                "clusters": [],
                "noise": {"size": 0, "members_count": 0},
            }

        clusters_obj = state.get("clusters") or {}
        clusters_list: List[Dict[str, Any]] = []

        for cid, c in clusters_obj.items():
            if not isinstance(c, dict):
                continue
            clusters_list.append({
                "cluster_id": cid,
                "size": int(c.get("size") or 0),
                "repr_doc_id": c.get("repr_doc_id"),
                "repr_preview": c.get("repr_preview") or "",
                "last_seen": c.get("last_seen"),
            })

        if sort_by == "size":
            key_fn = lambda x: x.get("size", 0)
        elif sort_by == "last_seen":
            key_fn = lambda x: (x.get("last_seen") or 0)
        else:
            key_fn = lambda x: x.get("cluster_id") or ""

        clusters_list.sort(key=key_fn, reverse=bool(descending))

        if limit and limit > 0:
            clusters_list = clusters_list[: int(limit)]

        noise = state.get("noise") or {}
        noise_size = int(noise.get("size") or 0)
        doc_to_cluster = state.get("doc_to_cluster") or {}

        out = {
            "plan_id": state.get("plan_id") or self.plan_spec.plan_id,
            "collection_name": state.get("collection_name") or self.plan_spec.collection_name,
            "version": state.get("version") or state.get("base_version"),
            "base_version": state.get("base_version"),
            "source": requested_source,
            "actual_source": actual_source,
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "time_range": state.get("time_range"),
            "method": state.get("method"),
            "params": state.get("params") or {},
            "n_points": int(state.get("n_points") or len(doc_to_cluster)),
            "n_clusters": int(state.get("n_clusters") or len(clusters_obj)),
            "n_noise": int(state.get("n_noise") or noise_size),
            "clusters": clusters_list,
        }

        if include_noise:
            out["noise"] = {"size": noise_size, "members_count": noise_size}

        return out

    def get_latest_clusters_summary(
            self,
            *,
            sort_by: str = "size",  # "size" | "last_seen" | "cluster_id"
            descending: bool = True,
            limit: int = 200,
            include_noise: bool = True,
    ) -> Dict[str, Any]:
        """
        Return a compact summary of clusters from latest offline result.

        Output schema (example):
        {
          "plan_id": ...,
          "collection_name": ...,
          "version": ...,
          "created_at": ...,
          "time_range": [..., ...] | None,
          "method": ...,
          "params": {...},
          "n_points": ...,
          "n_clusters": ...,
          "n_noise": ...,
          "clusters": [
             {"cluster_id": "cluster_0", "size": 10, "repr_doc_id": "...",
              "repr_preview": "...", "last_seen": 123.0},
             ...
          ],
          "noise": {"size": n, "members_count": n}   # 不返回 members 明细
        }
        """
        latest = self.get_latest_offline() or {}

        # When no offline exists yet
        if not latest or latest.get("version") is None:
            return {
                "plan_id": self.plan_spec.plan_id,
                "collection_name": self.plan_spec.collection_name,
                "version": None,
                "created_at": None,
                "time_range": None,
                "method": None,
                "params": None,
                "n_points": 0,
                "n_clusters": 0,
                "n_noise": 0,
                "clusters": [],
                "noise": {"size": 0, "members_count": 0},
            }

        clusters_obj = latest.get("clusters") or {}
        clusters_list: List[Dict[str, Any]] = []

        for cid, c in clusters_obj.items():
            if not isinstance(c, dict):
                continue
            clusters_list.append({
                "cluster_id": cid,
                "size": int(c.get("size") or 0),
                "repr_doc_id": c.get("repr_doc_id"),
                "repr_preview": c.get("repr_preview") or "",
                "last_seen": c.get("last_seen"),
            })

        # Sorting
        key_fn = None
        if sort_by == "size":
            key_fn = lambda x: x.get("size", 0)
        elif sort_by == "last_seen":
            key_fn = lambda x: (x.get("last_seen") or 0)
        else:
            key_fn = lambda x: x.get("cluster_id") or ""

        clusters_list.sort(key=key_fn, reverse=bool(descending))

        if limit and limit > 0:
            clusters_list = clusters_list[: int(limit)]

        noise = latest.get("noise") or {}
        noise_size = int(noise.get("size") or 0)

        out = {
            "plan_id": latest.get("plan_id") or self.plan_spec.plan_id,
            "collection_name": latest.get("collection_name") or self.plan_spec.collection_name,
            "version": latest.get("version"),
            "created_at": latest.get("created_at"),
            "time_range": latest.get("time_range"),
            "method": latest.get("method"),
            "params": latest.get("params") or {},
            "n_points": int(latest.get("n_points") or 0),
            "n_clusters": int(latest.get("n_clusters") or 0),
            "n_noise": int(latest.get("n_noise") or 0),
            "clusters": clusters_list,
        }

        if include_noise:
            out["noise"] = {"size": noise_size, "members_count": noise_size}

        return out

    # ----------------------------
    # Placeholder: appendix writeback (NOT implemented)
    # ----------------------------

    def writeback_appendix_placeholder(self, offline_result: Dict[str, Any]) -> None:
        """
        Placeholder only. No writeback implementation now.

        offline_result should include:
          plan_id, version, time_range, doc_to_cluster, clusters, collection_name
        """
        # TODO: implement later
        return
