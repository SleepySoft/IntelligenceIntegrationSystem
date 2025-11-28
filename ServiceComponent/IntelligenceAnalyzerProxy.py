import json
import time
import logging
import traceback
import json_repair
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ValidationError

from AIClientCenter.AIClientManager import BaseAIClient
from MyPythonUtility.FileSqliteHyridDB import HybridDB
from MyPythonUtility.DictTools import dict_list_to_markdown


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


MAX_OUTPUT_TOKEN = 8192         # The limit of Gemini
CONVERSATION_PATH = 'conversation'
conversation_db = HybridDB(CONVERSATION_PATH)


class AIMessage(BaseModel):
    UUID: str
    content: str
    title: str | None = None
    authors: List[str] = []
    pub_time: object | None = None
    informant: str | None = None


def extract_pure_response(text: str):
    while '<think>' in text and '</think>' in text:
        start_idx = text.find('<think>')
        end_idx = text.find('</think>', start_idx) + len('</think>')
        text = text[:start_idx] + text[end_idx:]
    text = text.replace('<answer>', '').replace('</answer>', '')
    return text.strip()


def extract_pure_json_text(text: str):
    return text.strip().removeprefix('```json').removesuffix('```').strip()


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
        f.write("[system]\n\n")
        f.write(messages[0]['content'])

        f.write("\n\n")
        f.write("[user]\n\n")
        f.write(messages[1]['content'])

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
    try:
        sanitized_data = AIMessage.model_validate(structured_data).model_dump(exclude_unset=True, exclude_none=True)
    except ValidationError as e:
        logger.error(f'AI require data field missing: {str(e)}')
        return {'error': str(e)}
    except Exception as e:
        logger.error(f'Validate AI data fail: {str(e)}')
        return {'error': str(e)}

    metadata_items = [f"- {k}: {v}" for k, v in sanitized_data.items() if k != "content"]
    metadata_block = '## metadata\n' + "\n".join(metadata_items)
    content_block = f"\n\n## 正文内容\n{sanitized_data['content']}"
    user_message = metadata_block + content_block

    messages = context if context else []
    messages.append({"role": "system", "content": prompt})
    messages.append({"role": "user", "content": user_message})

    start = time.time()

    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )

    elapsed = time.time() - start
    print(f"AI response spends {elapsed} s")

    return conversation_common_process('analysis', messages, response)


def aggressive_by_ai(
        ai_client: BaseAIClient,
        prompt: str,
        new_data: Dict[str, Any],
        history_data: List[Dict[str, str]]
) -> Dict:
    new_data_text = \
        f"{new_data['EVENT_TITLE']}\n\n"\
        f"{new_data['EVENT_BRIEF']}\n\n"
    history_data_md_table = dict_list_to_markdown(history_data)

    user_message = \
        f"# 新情报\n {new_data_text}"\
        f"# 历史情报\n {history_data_md_table}"

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_message}]

    start = time.time()

    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )

    elapsed = time.time() - start
    print(f"AI response spends {elapsed} s")

    return conversation_common_process('aggressive', messages, response)


def generate_recommendation_by_ai(
        ai_client: BaseAIClient,
        prompt: str,
        intelligence_list: List[Dict[str, str]]
) -> List[str] or Dict:

    intelligence_table = dict_list_to_markdown(intelligence_list)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": intelligence_table}]

    start = time.time()

    response = ai_client.chat(
        messages=messages,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKEN
    )

    elapsed = time.time() - start
    print(f"AI response spends {elapsed} s")

    return conversation_common_process('recommendation', messages, response)
