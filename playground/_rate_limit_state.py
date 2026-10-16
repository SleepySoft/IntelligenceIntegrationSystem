"""临时脚本：查看全部客户端限速器状态。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from AIClientCenter.factory_engine import load_config

clients, _ = load_config("_config/ai_clients.yaml")

for name, client in clients.items():
    snapshot = None
    getter = getattr(client, 'get_rate_limit_snapshot', None)
    if getter:
        snapshot = getter()
    if not snapshot:
        print(f'{name}: (no limiter)')
        continue
    print(f"{name}: limits={snapshot.get('effective_limits')} "
          f"used={snapshot.get('used')} remaining={snapshot.get('remaining')}")
