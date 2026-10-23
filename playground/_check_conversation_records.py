"""临时脚本：从真实对话记录实测最近调用的收发大小。"""
import re
from pathlib import Path

files = sorted(Path('conversation/analysis').glob('analysis_*.txt'))[-300:]

send_sizes, recv_sizes = [], []
for f in files:
    text = f.read_text(encoding='utf-8', errors='replace')
    # 文件格式：[user] ... [assistant] ...
    parts = re.split(r'\[(user|assistant|reply)\]', text)
    user_len = 0
    assistant_len = 0
    for i in range(1, len(parts) - 1, 2):
        role, content = parts[i], parts[i + 1]
        if role == 'user':
            user_len += len(content)
        elif role == 'assistant' or role == 'reply':
            assistant_len += len(content)
    if user_len:
        send_sizes.append(user_len)
    if assistant_len:
        recv_sizes.append(assistant_len)


def stats(name, values):
    if not values:
        print(name, '无数据')
        return
    values = sorted(values)
    n = len(values)
    print(f'{name}: n={n} min={values[0]} 中位={values[n // 2]} '
          f'p90={values[int(n * 0.9)]} max={values[-1]}')


stats('发送(user 消息, 字符)', send_sizes)
stats('接收(assistant 回复, 字符)', recv_sizes)

# 看一个完整文件的结构尾部（是否有 usage 信息）
tail = files[-1].read_text(encoding='utf-8', errors='replace')
print('\n最新文件尾部 500 字符:')
print(tail[-500:])
