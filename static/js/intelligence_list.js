/* static/js/intelligence_list.js */
document.addEventListener('DOMContentLoaded', () => {
    const API_URL = '/intelligences/query';

    const thresholdSelect = document.getElementById('threshold-select');
    const countSelect = document.getElementById('count-select');
    const refreshBtn = document.getElementById('refresh-btn');

    const DEFAULTS = {
        page: 1,
        per_page: 10,
        threshold: 0,      // 后端默认是 0
        search_mode: 'mongo',
        score_threshold: 0.6
    };

    const renderer = new ArticleRenderer('article-list-container', 'pagination-container');

    function getUrlState() {
        const params = new URLSearchParams(window.location.search);
        return {
            page: parseInt(params.get('page')) || DEFAULTS.page,
            per_page: parseInt(params.get('per_page')) || DEFAULTS.per_page,
            threshold: parseFloat(params.get('threshold')) || DEFAULTS.threshold,

            // 读取搜索模式和引用ID
            search_mode: params.get('search_mode') || DEFAULTS.search_mode,
            reference: params.get('reference') || '',
            score_threshold: parseFloat(params.get('score_threshold')) || DEFAULTS.score_threshold
        };
    }

    // updateUrl 需要保留这些特殊参数，否则翻页时会丢失
    function updateUrl(state) {
        const params = new URLSearchParams();

        if (state.page !== DEFAULTS.page) params.set('page', state.page);
        if (state.per_page !== DEFAULTS.per_page) params.set('per_page', state.per_page);
        if (state.threshold !== DEFAULTS.threshold) params.set('threshold', state.threshold);

        // 新增：如果当前是非 mongo 模式，必须保留 mode 和 reference 到 URL 中
        if (state.search_mode !== 'mongo') {
            params.set('search_mode', state.search_mode);
            if (state.reference) params.set('reference', state.reference);
            if (state.score_threshold) params.set('score_threshold', state.score_threshold);
        }

        const queryString = params.toString();
        const newUrl = queryString ? `${window.location.pathname}?${queryString}` : window.location.pathname;
        window.history.pushState({ path: newUrl }, '', newUrl);
    }

    function syncControls(state) {
        if (thresholdSelect && state.threshold !== undefined) {
            thresholdSelect.value = state.threshold;
        }
        if (countSelect && countSelect.querySelector(`option[value="${state.per_page}"]`)) {
            countSelect.value = state.per_page;
        }
    }

    async function loadData() {
        const state = getUrlState();
        syncControls(state);
        renderer.showLoading();

        // 构建请求参数，透传所有必要字段
        const requestParams = new URLSearchParams();

        requestParams.set('page', state.page);
        requestParams.set('per_page', state.per_page);
        requestParams.set('search_mode', state.search_mode); // 传给后端

        if (state.threshold > 0) requestParams.set('threshold', state.threshold);

        // 只有 vector_similar 模式才需要传 reference
        if (state.search_mode === 'vector_similar') {
            requestParams.set('reference', state.reference);
            requestParams.set('score_threshold', state.score_threshold);
        }

        const targetUrl = `${API_URL}?${requestParams.toString()}`;

        try {
            const response = await fetch(targetUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // body: JSON.stringify({}) // Body 为空，因为参数都在 URL query 中了
            });

            // [新增] 专门拦截 401 未登录状态
            if (response.status === 401) {
                const errData = await response.json();
                // 渲染器显示错误
                renderer.showError(`
                    <div class="text-center py-4">
                        <h4><i class="bi bi-lock-fill"></i> ${errData.error}</h4>
                        <p>${errData.message}</p>
                    </div>
                `);

                return; // 终止后续处理
            }

            if (!response.ok) throw new Error(`API Error: ${response.status}`);

            const data = await response.json();

            renderer.render(data.results, {
                total: data.total,
                page: state.page,       // 直接用当前的 page
                per_page: state.per_page // 直接用当前的 per_page
            });
        } catch (error) {
            console.error('Load Error:', error);
            renderer.showError(error.message);
        }
    }

    // --- 事件监听 ---

    function handleFilterChange() {
        // 每次筛选条件变化，重置回第一页
        const state = getUrlState();
        if (thresholdSelect) state.threshold = parseFloat(thresholdSelect.value);
        if (countSelect) state.per_page = parseInt(countSelect.value);

        state.page = 1; // 重置页码

        updateUrl(state);
        loadData();
    }

    if (thresholdSelect) thresholdSelect.addEventListener('change', handleFilterChange);
    if (countSelect) countSelect.addEventListener('change', handleFilterChange);
    if (refreshBtn) refreshBtn.addEventListener('click', loadData);

    document.body.addEventListener('click', (e) => {
        const target = e.target.closest('.page-btn');
        if (target && !target.classList.contains('disabled')) {
            e.preventDefault();
            const clickPage = parseInt(target.dataset.page);

            if (clickPage) {
                const state = getUrlState();
                state.page = clickPage;
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
