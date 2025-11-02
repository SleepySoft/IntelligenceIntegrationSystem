from newspaper import Article

# pip install newspaper3k

url = 'https://example-news.com/world/some-story'

# 实例化Article对象
article = Article(url)

# 1. 下载
article.download()

# 2. 解析 (这一步自动提取所有信息)
article.parse()

# 3. 获取信息
print(f"标题: {article.title}")
print(f"作者: {article.authors}")
print(f"发布日期: {article.publish_date}")
print(f"正文: \n{article.text}")

# 4. 它甚至可以做NLP
article.nlp()
print(f"摘要: {article.summary}")
print(f"关键词: {article.keywords}")