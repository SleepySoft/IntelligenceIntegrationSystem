"""Collector 到 Intelligence Hub 的出站提交适配器。"""

from typing import Any

from ServiceComponent.IntelligenceHubDefines_v4 import CollectedDataV4
from Tools.CommonPost import common_post


def build_collected_payload(data: CollectedDataV4) -> dict[str, Any]:
    """在 Collector 的 HTTP 边界生成可直接 JSON 编码的标准载荷。"""
    if not isinstance(data, CollectedDataV4):
        raise TypeError("Data must be CollectedDataV4 format.")
    return data.model_dump(mode="json", exclude_unset=True)


def post_collected_intelligence(
        url: str, data: CollectedDataV4, timeout: int = 10,
) -> dict[str, Any]:
    """规范化采集数据并提交到 Intelligence Hub 的 ``/collect``。"""
    try:
        payload = build_collected_payload(data)
    except TypeError as exc:
        return {"status": "error", "reason": str(exc)}
    return common_post(f"{url}/collect", payload, timeout)
