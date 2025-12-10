from ServiceComponent.ArticleTableRender import generate_articles_table, article_table_style, \
    article_table_color_gradient_script, article_source_enhancer_script


def default_article_list_render(articles, offset, count, total_count):
    """
    渲染带分页功能的HTML页面，主框架调用这个函数

    参数:
    articles -- 文章字典列表
    offset -- 当前偏移量
    count -- 每页显示数量
    total_count -- 文章总数
    """
    # 计算分页参数
    prev_offset = max(0, offset - count)
    next_offset = offset + count
    has_prev = offset > 0
    has_next = offset + count < total_count

    # 使用独立函数生成文章列表
    articles_html = generate_articles_table(articles)

    # 构建分页控件HTML
    pagination_html = ""
    if has_prev or has_next:
        pagination_html = f"""
        <div class="pagination">
            {f'<a href="/intelligences?offset=0&count={count}" class="page-btn head">1</a>' if has_prev else ''}
            {f'<a href="/intelligences?offset={prev_offset}&count={count}" class="page-btn prev">Prev</a>' if has_prev else ''}
            <span class="page-info">{offset // count + 1} / {(total_count + count - 1) // count}</span>
            {f'<a href="/intelligences?offset={next_offset}&count={count}" class="page-btn next">Next</a>' if has_next else ''}
        </div>
        """

    # 完整HTML结构
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Intelligence Integration System (IIS)</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            body {{ 
                font-family: 'Segoe UI', system-ui, sans-serif; 
                padding: 20px; 
                background-color: #f8f9fa;
            }}
            
            {article_table_style}
        
            .pagination {{
                display: flex;
                justify-content: center;
                align-items: center;
                margin-top: 10px;
                margin-bottom: 40px;
                gap: 15px;
            }}
            .page-btn {{
                padding: 8px 25px;
                background-color: #e3f2fd;
                border: 1px solid #bbdefb;
                border-radius: 6px;
                text-decoration: none;
                color: #0d47a1;
                transition: all 0.3s;
                font-weight: 500;
            }}
            .page-btn:hover {{
                background-color: #bbdefb;
                transform: translateY(-2px);
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            .page-info {{
                color: #546e7a;
                font-size: 0.95rem;
            }}
        </style>
    </head>
    <body>
        <div class="article-list">
            <h1>Intelligences</h1>
            {pagination_html}
            <div class="articles-container">
                {articles_html if articles else '<p class="text-center py-5">NO Intelligence</p>'}
            </div>
            {pagination_html}
        </div>
        
        {article_table_color_gradient_script}
        {article_source_enhancer_script}
    </body>
    </html>
    """

