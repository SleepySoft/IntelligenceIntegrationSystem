"""批量分析子系统的 IIS 装配入口（唯一耦合点）。

组合根调用 mount_to_iis()，传入 registry、pipeline 的 outcome 端口和 runtime 配置；
子系统内部不再 import 任何 Hub/管线实现。
"""

from __future__ import annotations

import logging
import os

from GlobalConfig import resolve_ai_client_config_file

from .config import BatchAnalysisConfig
from .service import BatchAnalysisService
from .web import create_blueprint

logger = logging.getLogger(__name__)


def find_mimo_api_key(config_path: str | None = None) -> str:
    """从 ai_clients.yaml 找 MiMo（batch 服务方）的按量 key。"""
    config_path = config_path or resolve_ai_client_config_file()
    try:
        import yaml
        cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
        for client in cfg.get("clients", []):
            if "xiaomimimo" in str(client.get("api_base_url", "")):
                return str(client.get("api_key") or "")
    except Exception:
        logger.warning("读取 ai_clients.yaml 寻找 batch key 失败", exc_info=True)
    return ""


def create_service(*, config, domain_resources=None, subsystem_registry=None, outcome_port,
                   data_path: str) -> BatchAnalysisService | None:
    """创建批量分析服务（装配期，HubApplication 构造前调用）。未启用/缺 key 返回 None。"""
    settings = BatchAnalysisConfig(config.get("batch_analysis", {}))
    if not settings.enabled:
        return None
    api_key = find_mimo_api_key()
    if not api_key:
        logger.warning("batch_analysis 已启用但 ai_clients.yaml 中没有 MiMo key，跳过。")
        return None
    return BatchAnalysisService(
        domain_resources or subsystem_registry, outcome_port,
        base_url=settings.base_url, model=settings.model,
        api_key=api_key,
        db_path=os.path.join(data_path, "analysis_batch.db"),
        lookback_days=settings.lookback_days,
        max_items=settings.max_items,
        poll_interval_sec=settings.poll_interval_sec,
    )


def mount_web(app, service, *, wrapper=None) -> None:
    """注册 HTTP 端点（hub_service 创建后调用）。"""
    if service is None:
        return
    app.register_blueprint(create_blueprint(service, wrapper))
    logger.info("Batch analysis subsystem web mounted.")
