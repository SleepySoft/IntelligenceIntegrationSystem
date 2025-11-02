import requests
from readability import Document

# pip install readability-lxml

url = 'https://example.blog.com/some-article'
response = requests.get(url)
html_content = response.text

doc = Document(html_content)

# 提取标题
print(f"标题: {doc.title()}")

# 提取正文HTML (保留了<p>, <a>等标签)
print(doc.summary())

# 提取纯文本正文 (需要自己再处理一下)
# print(doc.summary_html()) # 这是旧版API，新版用 summary()