"""生产分析和独立基准共同使用的无副作用消息构造器。"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ValidationError


logger = logging.getLogger(__name__)

ANALYSIS_MAX_OUTPUT_TOKENS = 8192


class AIMessage(BaseModel):
    UUID: str
    content: str
    title: str | None = None
    authors: List[str] = []
    pub_time: object | None = None
    informant: str | None = None


def extract_pure_response(text: str) -> str:
    while "<think>" in text and "</think>" in text:
        start_idx = text.find("<think>")
        end_idx = text.find("</think>", start_idx) + len("</think>")
        text = text[:start_idx] + text[end_idx:]
    return text.replace("<answer>", "").replace("</answer>", "").strip()


def extract_pure_json_text(text: str) -> str:
    return text.strip().removeprefix("```json").removesuffix("```").strip()


def build_analyze_user_message(structured_data: Dict[str, Any]) -> str:
    sanitized_data = AIMessage.model_validate(structured_data).model_dump(
        exclude_unset=True, exclude_none=True,
    )
    metadata_items = [f"- {key}: {value}" for key, value in sanitized_data.items()
                      if key != "content"]
    metadata_block = "## metadata\n" + "\n".join(metadata_items)
    content_block = f"\n\n## 正文内容\n{sanitized_data['content']}"
    return metadata_block + content_block


def build_analyze_message(
        prompt: str,
        structured_data: Dict[str, Any],
        context: Optional[List[Dict[str, str]]] = None) -> list[dict[str, str]]:
    """使用与生产分析完全相同的占位符和消息角色规则构造请求。"""
    has_content_placeholder = "{{CONTENT}}" in prompt
    try:
        user_message = build_analyze_user_message(structured_data)
    except ValidationError as exc:
        logger.error("AI require data field missing: %s", exc)
        raise
    except Exception as exc:
        logger.error("Validate AI data fail: %s", exc)
        raise

    current_date = datetime.date.today().strftime("%Y-%m-%d")
    prepared_prompt = (
        prompt
        .replace("{{CURRENT_DATE}}", current_date)
        .replace("{{SIMILAR_MESSAGES}}", "")
    )
    if has_content_placeholder:
        messages = list(context) if context else []
        messages.append({
            "role": "user",
            "content": prepared_prompt.replace("{{CONTENT}}", user_message),
        })
    else:
        # 缓存友好布局：稳定协议始终是首条 system；日期、正文和元数据全部位于
        # 最后一条动态 user。重试反馈追加在 prompt 末尾，不影响前缀复用。
        messages = [{"role": "system", "content": prepared_prompt}]
        if context:
            messages.extend(context)
        messages.append({
            "role": "user",
            "content": f"## 当前参考时间\n{current_date}\n\n{user_message}",
        })
    return messages
