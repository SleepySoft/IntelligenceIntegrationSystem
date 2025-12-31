/**
 * static/js/intelligence_search.js
 */

document.addEventListener('DOMContentLoaded', () => {

    // --- 1. 初始化 ---

    // 实例化渲染器
    // 参数1: 内容容器ID
    // 参数2: 分页容器的Class名 (注意HTML里要是 class="pagination-container")
    const renderer = new ArticleRenderer('article-list-content', 'pagination-container');

    const searchForm = document.getElementById('search-form');
    const searchButton = document.getElementById('search-button');
    const spinner = searchButton.querySelector('.spinner-border');

    // 整个结果区域的包装器
    const resultsWrapper = document.getElementById('results-wrapper');
    const resultsCountEl = document.getElementById('results-count');
    const resultsTotalEl = document.getElementById('results-total');

    // 存储当前查询状态
    let currentQueryState = {
        page: 1,
        per_page: 10,
        search_mode: 'mongo',
        payload_cache: {} // 缓存当前的搜索条件，翻页时使用
    };

    // --- 2. 核心功能 ---

    async function fetchResults(payload) {
        // UI Loading
        searchButton.disabled = true;
        spinner.classList.remove('d-none'); // Bootstrap 显隐类

        // 显示结果区域容器
        resultsWrapper.style.display = 'block';

        // 调用渲染器的 Loading (会显示 "Loading Intelligences...")
        renderer.showLoading();

        try {
            const response = await fetch('/intelligences/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `Server Error: ${response.status}`);
            }

            const data = await response.json();

            // 渲染数据
            renderer.render(data.results, {
                total: data.total,
                page: payload.page,
                per_page: payload.per_page
            });

            // 更新头部统计
            resultsCountEl.textContent = data.results.length;
            resultsTotalEl.textContent = data.total;

            // 自动滚动的搜索结果顶部，体验更好
            if(payload.page > 1) {
                 resultsWrapper.scrollIntoView({ behavior: 'smooth' });
            }

        } catch (error) {
            console.error('Fetch error:', error);
            renderer.showError(error.message);
            resultsTotalEl.textContent = '0';
            resultsCountEl.textContent = '0';
        } finally {
            // 恢复按钮状态
            searchButton.disabled = false;
            spinner.classList.add('d-none');
        }
    }

    // --- 3. 事件监听 ---

    // A. 表单提交
    searchForm.addEventListener('submit', (e) => {
        e.preventDefault();

        const formData = new FormData(searchForm);
        // 注意：FormData 对于未选中的 checkbox 不会包含 key，对于选中的值为 "on"

        // 1. 获取当前激活的 Tab 模式
        const activeTabBtn = document.querySelector('#search-mode-tabs .nav-link.active');
        const rawMode = activeTabBtn ? activeTabBtn.dataset.mode : 'mongo';

        // 模式映射：前端 'vector' -> 后端 'vector_text'
        const searchMode = rawMode === 'vector' ? 'vector_text' : 'mongo';

        // 2. 构建通用 Payload
        const payload = {
            page: 1,
            per_page: Number(formData.get('per_page')) || 10,
            search_mode: searchMode,
            // Keywords 在 Mongo 和 Vector 模式下都可能有用，统统传过去
            keywords: formData.get('keywords') || ''
        };

        // 3. 根据模式追加特定字段
        if (searchMode === 'vector_text') {
            payload.score_threshold = Number(formData.get('score_threshold')) || 0.5;

            // Checkbox 显式转 Boolean
            // formData.get('xx') 存在则为 'on' (truthy)，不存在则为 null (falsy)
            payload.in_summary = formData.get('in_summary') !== null;
            payload.in_fulltext = formData.get('in_fulltext') !== null;

        } else {
            // Mongo 模式
            if (formData.get('start_time')) payload.start_time = formData.get('start_time');
            if (formData.get('end_time')) payload.end_time = formData.get('end_time');

            // 字符串直接传，后端 _split 会处理逗号
            if (formData.get('locations')) payload.locations = formData.get('locations');
            if (formData.get('peoples')) payload.peoples = formData.get('peoples');
            if (formData.get('organizations')) payload.organizations = formData.get('organizations');

            // 确保 Mongo 模式下清理掉 Vector 特有的参数，避免混淆（虽然后端会忽略）
        }

        // 更新状态缓存
        currentQueryState.payload_cache = payload;
        currentQueryState.page = 1;
        currentQueryState.search_mode = searchMode;

        fetchResults(payload);
    });

    // B. 分页点击 (全局委托，适配 .page-btn)
    // 注意：这里监听的是 document.body 或者结果容器，确保能捕获到动态生成的按钮
    document.body.addEventListener('click', (e) => {
        // 匹配 .page-btn 而不是 .page-link
        const target = e.target.closest('.page-btn');

        if (target && !target.classList.contains('disabled')) {
            e.preventDefault();

            const clickPage = parseInt(target.dataset.page);
            if (clickPage && clickPage !== currentQueryState.page) {

                // 复制之前的搜索条件，仅修改页码
                const nextPayload = { ...currentQueryState.payload_cache };
                nextPayload.page = clickPage;

                // 更新状态
                currentQueryState.page = clickPage;

                fetchResults(nextPayload);
            }
        }
    });

});
