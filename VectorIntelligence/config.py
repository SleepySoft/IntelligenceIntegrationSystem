# VectorIntelligence/config.py
# 向量子系统配置：集合定义（单一事实源）与 VectorDB 连接设置读取。
#
# 集合名 / chunk 参数此前在 adapters/iis_vector.py、聚合 plan、
# Scripts/rebuild_vector_index.py 三处重复硬编码，现统一收敛到本模块。

from typing import Any, Dict, Optional

# ---------------- 向量集合（单一事实源） ----------------

SUMMARY_COLLECTION_NAME = "intelligence_summary"
SUMMARY_CHUNK_SIZE = 256
SUMMARY_CHUNK_OVERLAP = 30

FULL_TEXT_COLLECTION_NAME = "intelligence_full_text"
FULL_TEXT_CHUNK_SIZE = 512
FULL_TEXT_CHUNK_OVERLAP = 50

DEFAULT_VECTOR_STORES = (
    {
        "collection_name": SUMMARY_COLLECTION_NAME,
        "chunk_size": SUMMARY_CHUNK_SIZE,
        "chunk_overlap": SUMMARY_CHUNK_OVERLAP,
    },
    {
        "collection_name": FULL_TEXT_COLLECTION_NAME,
        "chunk_size": FULL_TEXT_CHUNK_SIZE,
        "chunk_overlap": FULL_TEXT_CHUNK_OVERLAP,
    },
)


def load_vector_db_settings(config) -> Optional[Dict[str, Any]]:
    """读取 ``intelligence_hub.vectordb`` 配置段。

    未启用或缺少关键配置时返回 None（向量子系统整体不装配）。
    """
    enabled = config.get('intelligence_hub.vectordb.enabled', False)
    vector_db_port = config.get('intelligence_hub.vectordb.vector_db_port', 8001)
    vector_db_path = config.get('intelligence_hub.vectordb.vector_db_path', '')
    embedding_model_name = config.get('intelligence_hub.vectordb.embedding_model_name', '')

    if not (enabled and vector_db_path and embedding_model_name):
        return None

    return {
        "url": f"http://localhost:{vector_db_port}",
        "db_path": vector_db_path,
        "embedding_model_name": embedding_model_name,
    }
