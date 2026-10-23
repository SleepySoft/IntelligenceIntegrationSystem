"""临时脚本：直接调用 chat 观察 local_rate_limit 的完整错误体。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AIClientCenter.factory_engine import build_manager, load_config
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V44

clients, limits = load_config("_config/ai_clients.yaml")
clients = {name: client for name, client in clients.items() if 'nvidia' in name.lower()}
groups = {client.group_id for client in clients.values()}
manager = build_manager(
    clients, {group: limit for group, limit in limits.items() if group in groups})

messages = [{"role": "user", "content": EVENT_ANALYSIS_PROMPT_V44.replace("{{CONTENT}}", "测试正文" * 50)}]
client = manager.wait_for_available_client("debug-rl", timeout=10)
print("client:", getattr(client, "name", "?"))
resp = client.chat(messages=messages, temperature=0, max_tokens=8192)
print(json.dumps(resp, ensure_ascii=False, default=str)[:1500])
