"""临时调试：定位 WebService 测试应用构建的卡点。"""
import faulthandler
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "Test"))

faulthandler.dump_traceback_later(40, exit=True)

from test_runtime_control import _build_app
from ServiceComponent.runtime.control import RuntimeControlState

print('building app...', flush=True)
app = _build_app(RuntimeControlState())
print('app built', flush=True)
client = app.test_client()
with client.session_transaction() as sess:
    sess['logged_in'] = True
r = client.get('/api/control/status')
print('status:', r.status_code, r.get_json(), flush=True)

r = client.post('/api/control/pause', json={'scope': 'intake'})
print('pause:', r.status_code, flush=True)
r = client.post('/collect', json={'UUID': 'u-1', 'token': 'collector-token'})
print('collect paused:', r.status_code, r.get_json(), flush=True)
r = client.post('/api/control/resume', json={'scope': 'intake'})
print('resume:', r.status_code, flush=True)
r = client.post('/collect', json={'UUID': 'u-1', 'token': 'collector-token'})
print('collect resumed:', r.status_code, r.get_json(), flush=True)

import threading as _t
for th in _t.enumerate():
    print('THREAD:', th.name, 'daemon=', th.daemon, 'alive=', th.is_alive(), flush=True)

