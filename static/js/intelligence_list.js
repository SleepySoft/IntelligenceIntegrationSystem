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
        search_mode: 'mongo'
    };

    const renderer = new ArticleRenderer('article-list-container', 'pagination-container');

    function getUrlState() {
        const params = new URLSearchParams(window.location.search);
        return {
            page: parseInt(params.get('page')) || DEFAULTS.page,
            per_page: parseInt(params.get('per_page')) || DEFAULTS.per_page,
            threshold: parseFloat(params.get('threshold')) || DEFAULTS.threshold
        };
    }

    function updateUrl(state) {
        const params = new URLSearchParams();

        if (state.page !== DEFAULTS.page) {
            params.set('page', state.page);
        }
        if (state.per_page !== DEFAULTS.per_page) {
            params.set('per_page', state.per_page);
        }
        if (state.threshold !== DEFAULTS.threshold) {
            params.set('threshold', state.threshold);
        }

        // 保持 URL 干净
        const queryString = params.toString();
        const newUrl = queryString ?
            `${window.location.pathname}?${queryString}` :
            window.location.pathname;

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

        const requestParams = new URLSearchParams();

        requestParams.set('page', state.page);
        requestParams.set('per_page', state.per_page);

        if (state.threshold > 0) requestParams.set('threshold', state.threshold);

        const targetUrl = `${API_URL}?${requestParams.toString()}`;

        try {
            const response = await fetch(targetUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // body: JSON.stringify({}) // Body 为空，因为参数都在 URL query 中了
            });

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
