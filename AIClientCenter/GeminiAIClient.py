import os

# ---------------------------------------------------------------------
# 【重要】在导入任何可能进行网络请求的库之前，首先设置环境变量
# 这样可以确保任何后续导入的模块都能看到这个设置
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:10809'
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:10809' # 同时设置HTTP和HTTPS是个好习惯


import google.generativeai as genai

# 1. 修正API Key的获取方式，去掉末尾的逗号
api_key = os.getenv("GEMINI_API_KEY")


os.putenv('HTTPS_PROXY', 'http://127.0.0.1:10809')

# 检查API Key是否存在
if not api_key:
    print("错误：请先设置 GEMINI_API_KEY 环境变量。")
    exit()

api_key = 'AIzaSyCqB9tToGgqBUrI6gcymxwPavIFG7f6RqQ'

try:
    # 2. 【重要】不要在这里传递 transport 或 httpx 客户端。
    #    库会自动从环境变量中读取代理设置。
    genai.configure(api_key=api_key)
except Exception as e:
    print(f"配置API密钥时出错: {e}")
    exit()

# 创建模型实例
try:
    # 打印所有可用的模型
    print("-" * 30)
    print("您的 API 密钥可以访问以下模型：")
    for m in genai.list_models():
        # 我们只关心支持内容生成的模型
        if 'generateContent' in m.supported_generation_methods:
            print(f"- {m.name}")
    print("-" * 30)

    print("创建模型实例...")
    model = genai.GenerativeModel('gemini-1.5-pro-latest')

    prompt = "你好，请介绍一下你自己以及你的主要能力。"

    print("正在向Gemini 1.5 Pro发送请求（将通过代理）...")
    response = model.generate_content(prompt)

    print("\nGemini 1.5 Pro 的回复：")
    print(response.text)

except Exception as e:
    print(f"调用模型时发生错误: {e}")
    print("可能的原因包括：")
    print("  - 代理服务器未运行或地址错误 (http://127.0.0.1:10809)")
    print("  - API密钥无效或已过期")
    print("  - 网络连接问题")