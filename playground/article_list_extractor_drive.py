# 您的代码...
# html_bytes = await page.content().then(lambda s: s.encode('utf-8'))
# url = page.url

extractor = ArticleListExtractor(verbose=True)

# --- 方式 A: 启发式 (Heuristic) ---
result = extractor.extract(html_bytes, url)

if result.success:
    print("启发式提取成功!")
    # result.metadata['extracted_links'] 包含了链接列表


# --- 方式 B: AI (AI-Powered) ---

# 步骤 1: 获取Prompt
prompt_result = extractor.extract(html_bytes, url, use_ai=True)

if prompt_result.metadata.get("ai_prompt_required"):
    ai_prompt = prompt_result.metadata["ai_prompt"]

    # *** 在这里调用您的AI服务 ***
    # ai_signature = your_ai_service.call(ai_prompt)
    ai_signature = "h2.post-title" # 假设AI返回了这个

    # 步骤 2: 传入AI的响应
    final_result = extractor.extract(
        html_bytes,
        url,
        use_ai=True,
        ai_signature=ai_signature
    )

    if final_result.success:
        print("AI 提取成功!")
        # final_result.metadata['extracted_links'] 包含了链接列表