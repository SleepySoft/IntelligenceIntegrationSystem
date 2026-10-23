"""Collector 到 Intelligence Hub 的出站提交适配器。"""

from typing import Any, Tuple

from ServiceComponent.IntelligenceHubDefines_v4 import CollectedData
from Tools.CommonPost import common_post


def build_collected_payload(data: CollectedData) -> dict[str, Any]:
    """在 Collector 的 HTTP 边界生成可直接 JSON 编码的标准载荷。"""
    if not isinstance(data, CollectedData):
        raise TypeError("Data must be CollectedData format.")
    return data.model_dump(mode="json", exclude_unset=True)


def post_collected_intelligence(
        url: str, data: CollectedData, timeout: int = 10,
) -> dict[str, Any]:
    """规范化采集数据并提交到 Intelligence Hub 的 ``/collect``。"""
    try:
        payload = build_collected_payload(data)
    except TypeError as exc:
        return {"status": "error", "reason": str(exc)}
    return common_post(f"{url}/collect", payload, timeout)


def normalize_collect_response(result: Any) -> Tuple[str, str]:
    """把 /collect 的各种历史/异常应答规范为 (status, reason)。

    status ∈ queued | rejected | error：
    - queued：服务端已接收；
    - rejected：服务端明确拒绝（重复、校验失败、无效 token 等），重试无意义；
    - error：传输或服务端内部错误（含 maintenance），可缓存重试。
    """
    if not isinstance(result, dict):
        return 'error', f'unexpected response: {type(result).__name__}'
    if result.get('status') == 'error':     # common_post 的传输/HTTP 层错误
        return 'error', str(result.get('reason') or 'transport_error')
    resp = result.get('resp', result.get('status', ''))
    # 兼容历史 bug：resp 曾是单元素数组（如 ['queued']）。
    if isinstance(resp, (list, tuple)):
        resp = resp[0] if resp else ''
    resp = str(resp or '').strip().lower()
    reason = str(result.get('reason') or '').strip()
    if resp in ('queued', 'ok', 'success'):
        return 'queued', ''
    if resp == 'rejected':
        return 'rejected', reason or 'rejected'
    if resp == 'invalid token':             # 旧服务端应答格式
        return 'rejected', 'invalid_token'
    return 'error', reason or (resp or 'empty_response')
