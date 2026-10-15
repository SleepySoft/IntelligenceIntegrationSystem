"""临时脚本：查看最新对话记录文件的新格式。"""
import sqlite3

conn = sqlite3.connect('conversation/index.sqlite')
row = conn.execute("SELECT relative_path FROM records ORDER BY id DESC LIMIT 1").fetchone()
print('最新记录:', row[0])
path = 'conversation/' + row[0].replace('\\', '/')
text = open(path, encoding='utf-8').read()
print('文件大小:', len(text))
print('--- 开头 700 字符 ---')
print(text[:700])
print('--- 尾部 300 字符 ---')
print(text[-300:])
