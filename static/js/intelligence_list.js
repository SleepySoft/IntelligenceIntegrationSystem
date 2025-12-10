/* static/js/intelligence_list.js */
document.addEventListener('DOMContentLoaded', () => {
    // ... 配置部分不变 ...
    const API_URL = '/intelligences';
    const thresholdSelect = document.getElementById('threshold-select');
    const countSelect = document.getElementById('count-select');
    const refreshBtn = document.getElementById('refresh-btn');

    // 注意：这里第二个参数传 class 名，因为我们要同时控制顶部和底部两个分页栏
    const renderer = new ArticleRenderer('article-list-container', 'pagination-container');

    // ... getUrlState, updateUrl, syncControls, loadData 函数保持不变 ...
    function getUrlState() {
        const params = new URLSearchParams(window.location.search);
        return {
            offset: parseInt(params.get('offset')) || 0,
            count: parseInt(params.get('count')) || 10,
            threshold: parseInt(params.get('threshold')) || 6
        };
    }

    function updateUrl(state) {
        const params = new URLSearchParams();
        params.set('offset', state.offset);
        params.set('count', state.count);
        params.set('threshold', state.threshold);
        const newUrl = `${window.location.pathname}?${params.toString()}`;
        window.history.pushState({ path: newUrl }, '', newUrl);
    }

    function syncControls(state) {
        if (thresholdSelect.querySelector(`option[value="${state.threshold}"]`)) {
            thresholdSelect.value = state.threshold;
        }
        if (countSelect.querySelector(`option[value="${state.count}"]`)) {
            countSelect.value = state.count;
        }
    }

    async function loadData() {
        // 1. 获取当前状态（来源于浏览器地址栏，因为 updateUrl 已经在之前执行了）
        const state = getUrlState();
        syncControls(state);
        renderer.showLoading();

        // 2. [关键修改] 构建发送给服务器的 URL 参数
        // 即使是 POST 请求，也可以带 URL 参数 (Query String)
        const queryParams = new URLSearchParams({
            offset: state.offset,
            count: state.count,
            threshold: state.threshold
        });

        // 3. 构建 URL：将参数拼接到 API_URL 后面
        // 结果类似：/intelligences?offset=0&count=10&threshold=6
        const targetUrl = `${API_URL}?${queryParams.toString()}`;

        // 4. 准备 Body (用于放置那些不适合放在 URL 里的复杂或过长参数)
        // 注意：根据上一轮的后端代码，后端是用 'offset' 和 'count'，
        // 但你这里传的是 'page' 和 'per_page'。为了保险，建议 Body 里也保持一致。
        const payload = {
            // 冗余发送，确保万无一失，但后端现在的逻辑会优先取 URL 里的
            offset: state.offset,
            count: state.count,
            threshold: state.threshold,

            // 其他不需要体现在 URL 上的参数继续放在 Body 里
            search_mode: 'mongo',
            start_time: '',
            end_time: '',
            locations: '',
            peoples: '',
            organizations: '',
            keywords: '',
            score_threshold: 0.5
        };

        try {
            // 5. 发起请求：使用带参数的 targetUrl
            const response = await fetch(targetUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) throw new Error(`API Error: ${response.status}`);

            const data = await response.json();

            // 计算当前页码用于前端分页渲染
            const currentPage = Math.floor(state.offset / state.count) + 1;

            renderer.render(data.results, {
                total: data.total,
                page: currentPage,
                per_page: state.count
            });
        } catch (error) {
            console.error('Load Error:', error);
            renderer.showError(error.message);
        }
    }

    // --- 事件监听 ---

    // 筛选器变化
    function handleFilterChange() {
        const state = getUrlState();
        state.threshold = parseInt(thresholdSelect.value);
        state.count = parseInt(countSelect.value);
        state.offset = 0;
        updateUrl(state);
        loadData();
    }

    if (thresholdSelect) thresholdSelect.addEventListener('change', handleFilterChange);
    if (countSelect) countSelect.addEventListener('change', handleFilterChange);
    if (refreshBtn) refreshBtn.addEventListener('click', loadData);

    // [关键修改] 分页点击事件委托
    // 因为现在分页按钮是 .page-btn，且没有 .page-link 类了
    document.body.addEventListener('click', (e) => {
        // 查找是否点击了 .page-btn
        const target = e.target.closest('.page-btn');
        if (target && !target.classList.contains('disabled')) {
            e.preventDefault();
            const clickPage = parseInt(target.dataset.page);
            if (clickPage) {
                const state = getUrlState();
                state.offset = (clickPage - 1) * state.count;
                updateUrl(state);
                loadData();

                // 自动回到顶部，体验更好
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }
    });

    window.addEventListener('popstate', loadData);
    loadData();
});
