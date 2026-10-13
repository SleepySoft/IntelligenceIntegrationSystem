import json
import logging
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


def record_conversation(category: str, messages: list, response: dict) -> int:
    # folder_path = os.path.join('conversation', folder)
    # os.makedirs(folder_path, exist_ok=True)
    # file_path = os.path.join(folder_path, f"conversation_{datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}.txt")
    #
    # with open(file_path, 'wt', encoding='utf-8') as f:

    writer = conversation_db.raw_file(
        content_type='text',
        category=category,
        name=category)

    with writer as f:
        index = 0

        if len(messages) > 1:
            f.write("[system]\n\n")
            f.write(messages[index]['content'])
            index += 1

        f.write("\n\n")
        f.write("[user]\n\n")
        f.write(messages[index]['content'])

        f.write("\n\n")
        f.write("[reply]\n\n")
        if isinstance(response, Dict) and "choices" in response:
            f.write(response["choices"][0]["message"]["content"])
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


def conversation_common_process(category, messages, response) -> dict:
    if 'error' in response:
        logger.error(f'Get error from response.')
        return response

    record_index = record_conversation(category, messages, response)
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
        context: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    Use the OpenAI API to analyze the input prompt and structured data, and return a formatted JSON result.

    Args:
    ai_client (OpenAIClient): Provides a client instance of the OpenAI compatible API.
    prompt (str): The main prompt, used to specify the role and rules for analysis.
    structured_data (Dict[str, Any]): Structured data, which must contain the 'content' field of the main content.
    context (Optional[List[Dict[str, str]]]): Dialogue context, optional.

    Returns:
    Dict[str, Any]: JSON object processed by AI, converted to a Python dictionary.
    """
    messages = build_analyze_message(prompt, structured_data, context)

    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )

    return conversation_common_process('analysis', messages, response)


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

