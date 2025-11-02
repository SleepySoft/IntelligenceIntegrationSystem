import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from pydantic import BaseModel, Field

# pip install litellm  # 安装 LiteLLM 来管理 AI API
# export OPENAI_API_KEY="sk-your-real-openai-api-key"

# 1. 定义你想要的数据结构 (Schema)
class ProductInfo(BaseModel):
    product_name: str = Field(description="The name of the product")
    price: float = Field(description="The price of the product")

# 2. 定义 AI 提取策略
# 你需要告诉它使用哪个模型，以及你想要的数据结构
llm_strategy = LLMExtractionStrategy(
    provider="openai/gpt-4o",  # 指定模型
    schema=ProductInfo.model_json_schema(), # 传入你的数据结构
    instruction="Extract the product name and price from the page." # 提示词
)

# 3. 运行爬虫，并传入这个AI策略
async def main():
    url = "https://example-product-page.com"

    crawler = AsyncWebCrawler()

    # 传入配置，告诉爬虫这次要用 LLM 策略
    config = CrawlerRunConfig(extraction_strategy=llm_strategy)

    result = await crawler.arun(url=url, config=config)

    # result.extracted_content 现在是一个 JSON 字符串
    print("--- 结构化 AI 提取结果 ---")
    print(result.extracted_content)
    # 可能输出: {"product_name": "Super Widget", "price": 49.99}

if __name__ == "__main__":
    asyncio.run(main())