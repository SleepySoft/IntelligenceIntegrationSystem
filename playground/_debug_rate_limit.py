"""临时脚本：直接调用 chat 观察 local_rate_limit 的完整错误体。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AIClientCenter.core.manager import AIClientManager
from _config.ai_client_config import AI_CLIENTS, AI_CLIENT_LIMIT
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V44

manager = AIClientManager()
for name, client in AI_CLIENTS.items():
    if 'nvidia' not in name.lower():
        continue
    manager.register_client(client)
for group, limit in AI_CLIENT_LIMIT.items():
    manager.set_group_limit(group, limit)

messages = [{"role": "user", "content": EVENT_ANALYSIS_PROMPT_V44.replace("{{CONTENT}}", "测试正文" * 50)}]
client = manager.wait_for_available_client("debug-rl", timeout=10)
print("client:", getattr(client, "name", "?"))
resp = client.chat(messages=messages, temperature=0, max_tokens=8192)
print(json.dumps(resp, ensure_ascii=False, default=str)[:1500])
