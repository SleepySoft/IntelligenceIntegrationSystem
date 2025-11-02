import requests
import trafilatura

# pip install trafilatura

# 1. 抓取网页HTML
url = 'https://example.blog.com/some-article'
response = requests.get(url)
html_content = response.text

# 2. 提取正文 (就这么简单)
# trafilatura.extract 会返回提取出的正文文本
main_text = trafilatura.extract(html_content)

# 3. 提取带元数据的结构化信息 (推荐)
# 返回一个包含标题、作者、日期、正文等的字典
result = trafilatura.extract(html_content,
                             output_format='json',
                             include_metadata=True)
import json
print(json.dumps(result, indent=2, ensure_ascii=False))

# 可能的输出：
# {
#   "title": "文章标题",
#   "author": "作者名",
#   "date": "2025-11-01",
#   "text": "这里是提取出的所有正文内容...",
#   "url": "https://example.blog.com/some-article"
# }