# VectorIntelligence/search.py
# 向量搜索业务（单一实现）：召回 → Mongo 回填 → vector_score 注入 → 排序 → 分页。
#
# 抽取自 IntelligenceHubWebService._do_vector_search，供 Core Web 委托与
# /vector/ Blueprint 共用。依赖全部以可调用注入，不 import Core Web 模块。

import datetime
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from VectorIntelligence.vector_engine import IntelligenceVectorDBEngine
from ServiceComponent.intelligence_projections import (
    IntelligenceProjectionProfile,
    card_with_vector_score,
)

logger = logging.getLogger(__name__)

# 与 Core Web 层保持一致的上限/默认阈值（历史值）。
VECTOR_MAX_TOP_N = 50
VECTOR_DEFAULT_SCORE_THRESHOLD = 0.6


def do_vector_search(p: Dict[str, Any], *,
                     vector_search: Callable[..., List[Tuple[str, float, dict]]],
                     get_intelligence: Callable[..., Any],
                     domain_name: Optional[str] = None,
                     default_domain_name: Optional[str] = None,
                     ) -> Tuple[List[dict], int]:
    """走向量召回 + 内存分页（向量检索当前仅默认情报域启用）。

    参数与返回与历史 IntelligenceHubWebService._do_vector_search 完全一致：
    返回 (articles, total)，其中 total 为召回总数（封顶 top_n），
    articles 已注入查询时临时字段 vector_score（不写回权威归档）。
    """
    if default_domain_name and domain_name and domain_name != default_domain_name:
        raise ValueError("向量检索暂仅支持默认情报域。")

    text = ''
    if p['search_mode'] == 'vector_text':
        text = p.get('keywords', '')
    elif p['search_mode'] == 'vector_similar':
        if ref_uuids := p.get('reference', ''):
            intelligence = get_intelligence(
                ref_uuids, domain_name=domain_name,
                profile=IntelligenceProjectionProfile.CARD)
            if intelligence:
                text = IntelligenceVectorDBEngine.build_search_text(intelligence, 'summary')
    if not text:
        return [], 0

    top_n = p.get('_effective_top_n') or min(p['page'] * p['per_page'], VECTOR_MAX_TOP_N)
    vector_kwargs = {
        'text': text,
        'in_summary': p['in_summary'],
        'in_fulltext': p['in_fulltext'],
        'top_n': top_n,
        'score_threshold': p.get('score_threshold_min', VECTOR_DEFAULT_SCORE_THRESHOLD),
        'score_threshold_max': p.get('score_threshold_max', 1.0),
    }

    if p['start_time'] and p['end_time']:
        vector_kwargs['event_period'] = (
            datetime.datetime.fromisoformat(p['start_time']),
            datetime.datetime.fromisoformat(p['end_time'])
        )
    if p['archive_start_time'] and p['archive_end_time']:
        vector_kwargs['archive_period'] = (
            datetime.datetime.fromisoformat(p['archive_start_time']),
            datetime.datetime.fromisoformat(p['archive_end_time'])
        )

    raw: List[Tuple[str, float, dict]] = vector_search(**vector_kwargs)

    start = (p['page'] - 1) * p['per_page']
    end = start + p['per_page']
    page_items = raw[start:end]

    uuids = []
    score_map = {}
    for doc_id, score, _ in page_items:
        uuids.append(doc_id)
        score_map[doc_id] = score
    articles = get_intelligence(
        uuids, domain_name=domain_name,
        profile=IntelligenceProjectionProfile.CARD)

    articles = [
        card_with_vector_score(
            article, score_map.get(str(article.get('UUID') or ''), 0.0))
        for article in articles
    ]

    articles.sort(key=lambda x: x.get('vector_score', 0.0), reverse=True)

    return articles, len(raw)


def build_vector_search_handler(*, hub) -> Callable[..., Tuple[List[dict], int]]:
    """生成供 Core Web 注入的向量搜索 handler（签名与 _do_vector_search 一致）。

    经 hub 门面访问向量检索与 Mongo 回填，VectorDB 未配置时门面返回空列表，
    行为与历史 fallback 一致。
    """
    def handler(p: Dict[str, Any], domain_name: Optional[str] = None) -> Tuple[List[dict], int]:
        return do_vector_search(
            p,
            vector_search=hub.vector_search_intelligence,
            get_intelligence=hub.get_intelligence,
            domain_name=domain_name,
            default_domain_name=hub.default_domain_name,
        )

    return handler
