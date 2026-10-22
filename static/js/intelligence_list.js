/* static/js/intelligence_list.js */
document.addEventListener('DOMContentLoaded', () => {
    const API_URL = (window.IIS_BASE_PATH || '') + '/intelligences/query';

    const thresholdSelect = document.getElementById('threshold-select');
    const countSelect = document.getElementById('count-select');
    const sortSelect = document.getElementById('sort-select');
    const refreshBtn = document.getElementById('refresh-btn');
    const publicMode = window.IIS_PUBLIC_MODE === true;
    const publicLimits = window.IIS_PUBLIC_LIMITS || {};
    const publicMaxPerPage = Math.max(1, Number(publicLimits.mongo_max_per_page) || 20);
    const defaultPerPage = publicMode ? Math.min(20, publicMaxPerPage) : 10;
    const embeddedInViews = window.parent !== window;

    const DEFAULTS = {
        page: 1,
        per_page: defaultPerPage,
        threshold: 0,      // 后端默认是 0
        sort_by: 'archived_at',
        search_mode: 'mongo',
        score_threshold: 0.6
    };

    const renderer = new ArticleRenderer('article-list-container', 'pagination-container');

    if (publicMode && countSelect) {
        [...countSelect.options].forEach(option => {
            if (Number(option.value) > publicMaxPerPage) option.remove();
        });
        if (!countSelect.querySelector(`option[value="${defaultPerPage}"]`)) {
            countSelect.add(new Option(String(defaultPerPage), String(defaultPerPage)));
        }
    }

    if (window.ArticleModalManager) {
        ArticleModalManager.init({
            history: true
        });
    }

    function getUrlState() {
        const params = new URLSearchParams(window.location.search);
        const state = {
            page: parseInt(params.get('page')) || DEFAULTS.page,
            per_page: parseInt(params.get('per_page')) || DEFAULTS.per_page,
            threshold: parseFloat(params.get('threshold')) || DEFAULTS.threshold,
            sort_by: params.get('sort_by') === 'content_time' ? 'content_time' : DEFAULTS.sort_by,

            // 读取搜索模式和引用ID
            search_mode: params.get('search_mode') || DEFAULTS.search_mode,
            reference: params.get('reference') || '',
            score_threshold: parseFloat(params.get('score_threshold')) || DEFAULTS.score_threshold
        };
        state.page = Math.max(1, state.page);
        state.per_page = Math.max(1, Math.min(
            publicMode ? publicMaxPerPage : 100, state.per_page));
        return state;
    }

    // updateUrl 需要保留这些特殊参数，否则翻页时会丢失
    function publishListState(state, historyMode) {
        if (!embeddedInViews) return;
        window.parent.postMessage({
            type: 'iis:list-state',
            history_mode: historyMode,
            state: {
                page: state.page,
                per_page: state.per_page,
                threshold: state.threshold,
                sort_by: state.sort_by,
                search_mode: state.search_mode,
                reference: state.reference,
                score_threshold: state.score_threshold,
            },
        }, window.location.origin);
    }

    function updateUrl(state, historyMode = 'push') {
        const params = new URLSearchParams();

        if (state.page !== DEFAULTS.page) params.set('page', state.page);
        if (state.per_page !== DEFAULTS.per_page) params.set('per_page', state.per_page);
        if (state.threshold !== DEFAULTS.threshold) params.set('threshold', state.threshold);
        if (state.sort_by !== DEFAULTS.sort_by) params.set('sort_by', state.sort_by);

        // 新增：如果当前是非 mongo 模式，必须保留 mode 和 reference 到 URL 中
        if (state.search_mode !== 'mongo') {
            params.set('search_mode', state.search_mode);
            if (state.reference) params.set('reference', state.reference);
            if (state.score_threshold) params.set('score_threshold', state.score_threshold);
        }

        const queryString = params.toString();
        const newUrl = queryString ? `${window.location.pathname}?${queryString}` : window.location.pathname;
        if (embeddedInViews) {
            // 外层 /views URL 是可分享的单一事实源；iframe 自身只替换地址，
            // 避免一次翻页在联合浏览历史中产生两个记录。
            window.history.replaceState({ path: newUrl }, '', newUrl);
            publishListState(state, historyMode);
        } else {
            window.history[historyMode === 'replace' ? 'replaceState' : 'pushState'](
                { path: newUrl }, '', newUrl);
        }
    }

    function syncControls(state) {
        if (thresholdSelect && state.threshold !== undefined) {
            thresholdSelect.value = state.threshold;
        }
        if (countSelect && countSelect.querySelector(`option[value="${state.per_page}"]`)) {
            countSelect.value = state.per_page;
        }
        if (sortSelect) {
            sortSelect.value = state.sort_by;
            sortSelect.disabled = state.search_mode !== 'mongo';
            sortSelect.title = sortSelect.disabled ? '向量检索按相似度排序' : '';
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
        requestParams.set('sort_by', state.sort_by);

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

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `API Error: ${response.status}`);
            }

            const data = await response.json();
            const effectivePage = Number(data.page) || state.page;
            const effectivePerPage = Number(data.per_page) || state.per_page;
            if (effectivePage !== state.page || effectivePerPage !== state.per_page) {
                state.page = effectivePage;
                state.per_page = effectivePerPage;
                syncControls(state);
                updateUrl(state, 'replace');
            }

            renderer.setSortBy(state.search_mode === 'mongo' ? state.sort_by : 'relevance');
            renderer.render(data.results, {
                total: data.total,
                page: effectivePage,
                per_page: effectivePerPage,
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
        if (sortSelect) state.sort_by = sortSelect.value;

        state.page = 1; // 重置页码

        updateUrl(state);
        loadData();
    }

    if (thresholdSelect) thresholdSelect.addEventListener('change', handleFilterChange);
    if (countSelect) countSelect.addEventListener('change', handleFilterChange);
    if (sortSelect) sortSelect.addEventListener('change', handleFilterChange);
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

    window.addEventListener('popstate', (e) => {
        // 1. 如果是从 Modal 弹窗退回来的，拦截器生效，不刷新列表
        if (window._preventListReload) {
            window._preventListReload = false; // 消费掉这个标记
            return;
        }

        // 2. 如果用户按了浏览器的“前进”键，去到了 /intelligence/xxx，也不刷新列表
        if (location.pathname.startsWith('/intelligence/')) {
            return;
        }

        // 3. 只有正常的列表翻页、筛选条件的回退，才真正刷新数据
        loadData();
    });

    publishListState(getUrlState(), 'replace');
    loadData();
});
