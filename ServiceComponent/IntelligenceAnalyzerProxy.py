import datetime
import json
import logging
import time
import traceback
import json_repair
from typing import Optional, Dict, Any, List

from AIClientCenter.core.manager import BaseAIClient
from MyPythonUtility.FileSqliteHyridDB import HybridDB
from ServiceComponent.analysis_messages import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    AIMessage,
    build_analyze_message,
    build_analyze_user_message,
    extract_pure_json_text,
    extract_pure_response,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


MAX_OUTPUT_TOKEN = ANALYSIS_MAX_OUTPUT_TOKENS
CONVERSATION_PATH = 'conversation'
conversation_db = HybridDB(CONVERSATION_PATH)


def record_conversation(category: str, messages: list, response: dict, meta: dict = None) -> int:
    """记录一次 AI 交互：原始请求、原始响应、结构化元数据。

    文件格式（2026-10-15 起，旧文件为 [system]/[user]/[reply] 纯文本）：

        [meta]      单行 JSON：时间、类别、模型、usage、耗时、调用参数及调用方附加信息
        [request]   原始请求消息（按 [system]/[user] 分块，内容与发送一致）
        [response]  完整原始响应 JSON（含 usage、model、客户端元数据）
    """
    writer = conversation_db.raw_file(
        content_type='text',
        category=category,
        name=category)

    with writer as f:
        f.write("[meta]\n")
        meta_payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "category": category,
            "model": response.get("model") if isinstance(response, Dict) else None,
            "usage": response.get("usage") if isinstance(response, Dict) else None,
            "client_meta": (response or {}).get("_ai_client_meta")
            if isinstance(response, Dict) else None,
        }
        if meta:
            meta_payload.update(meta)
        f.write(json.dumps(meta_payload, ensure_ascii=False, default=str))

        f.write("\n\n")
        f.write("[request]\n")
        for message in messages:
            f.write(f"\n[{message.get('role', 'unknown')}]\n\n")
            f.write(str(message.get('content', '')))

        f.write("\n\n")
        f.write("[response]\n")
        if isinstance(response, Dict):
            f.write(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        else:
            f.write('<None>')

    return writer.index


def parse_ai_response(response: dict) -> dict:
    if isinstance(response, Dict) and "choices" in response:
        ai_output = response["choices"][0]["message"]["content"]
        ai_answer = extract_pure_response(ai_output)
        ai_json = extract_pure_json_text(ai_answer)

        try:
            parsed_output = json.loads(ai_json)
            return parsed_output

        except json.JSONDecodeError:
            logger.warning(f'Error when parsing AI reply to json, try to repair...')

            try:
                repaired_data = json_repair.loads(ai_json)
                fixed_json_str = json.dumps(repaired_data, ensure_ascii=False, indent=4)

                parsed_output = json.loads(fixed_json_str)
                if isinstance(parsed_output, dict):
                    parsed_output['warning'] = 'Json repaired.'

                logger.info(f'Json repare success.')

                return parsed_output
            except json.JSONDecodeError:
                logger.error(f'Json cannot be repaired.')
                return {'error': "Cannot parse AI response to JSON."}

            except:
                raise

        except Exception as e:
            logger.error(f'Exception when parsing AI response')
            print(traceback.format_exc())

    else:
        return {'error': "Invalid AI response."}


def conversation_common_process(category, messages, response, meta: dict = None) -> dict:
    if 'error' in response:
        logger.error(f'Get error from response.')
        return response

    record_index = record_conversation(category, messages, response, meta=meta)
    ai_json = parse_ai_response(response)

    record = conversation_db.get_by_index(record_index, False)
    if record:
        record_file_rel_path = record['path']
        record_file_web_path = f"{CONVERSATION_PATH}/{record_file_rel_path}"
        record_file_web_path = record_file_web_path.replace('\\', '/')
    else:
        record_file_rel_path = ''
        record_file_web_path = ''
    if isinstance(ai_json, dict):
        # For recommendation, it's not a dict.
        ai_json['record_file'] = record_file_rel_path

    if isinstance(ai_json, dict) and 'error' in ai_json:
        logger.error(f'AI {category} conversation fail.', extra={'link_file': record_file_web_path})
    else:
        logger.debug(f'AI {category} conversation successful.', extra={'link_file': record_file_web_path})

    return ai_json


def analyze_with_ai(
        ai_client: BaseAIClient,
        prompt: str,
        structured_data: Dict[str, Any],
        context: Optional[List[Dict[str, str]]] = None,
        record_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Use the OpenAI API to analyze the input prompt and structured data, and return a formatted JSON result.

    Args:
    ai_client (OpenAIClient): Provides a client instance of the OpenAI compatible API.
    prompt (str): The main prompt, used to specify the role and rules for analysis.
    structured_data (Dict[str, Any]): Structured data, which must contain the 'content' field of the main content.
    context (Optional[List[Dict[str, str]]]): Dialogue context, optional.
    record_meta (Optional[Dict]): 附加到对话记录 [meta] 的信息（如 prompt_version、uuid）。

    Returns:
    Dict[str, Any]: JSON object processed by AI, converted to a Python dictionary.
    """
    messages = build_analyze_message(prompt, structured_data, context)

    started = time.perf_counter()
    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    meta = {
        "elapsed_ms": elapsed_ms,
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKEN,
    }
    if record_meta:
        meta.update(record_meta)
    return conversation_common_process('analysis', messages, response, meta=meta)


def analyze_with_ai_transient(
        ai_client: BaseAIClient,
        prompt: str,
        structured_data: Dict[str, Any],
        context: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """执行一次不记录 conversation 文件/数据库的临时分析。"""
    messages = build_analyze_message(prompt, structured_data, context)
    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )
    if not isinstance(response, Dict):
        return {'error': "Invalid AI response."}
    if 'error' in response:
        return response
    return parse_ai_response(response)

