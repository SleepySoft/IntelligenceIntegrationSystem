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

    loadData();

    // Modal 管理器
    (function initArticleDetailModal() {
        const overlay = document.getElementById('article-detail-overlay');
        const bodyEl = document.getElementById('article-modal-body');
        const titleEl = document.getElementById('article-modal-title');
        const uuidEl = document.getElementById('article-modal-uuid');
        const closeBtn = document.getElementById('article-close-btn');
        const copyBtn = document.getElementById('article-copy-link-btn');
        const openNewBtn = document.getElementById('article-open-newtab-btn');

        if (!overlay || !bodyEl || !titleEl || !uuidEl || !closeBtn || !copyBtn || !openNewBtn) return;

        let lastFocus = null;
        let isOpen = false;
        let pushedState = false; // [修复] 记录是否改变了 URL

        async function openByUuid(uuid) {
            const pageUrl = `/intelligence/${encodeURIComponent(uuid)}`;
            await open(pageUrl, uuid, 'Detail');
        }

        async function open(pageUrl, uuid, titleFallback) {
            overlay.style.display = 'flex';
            overlay.setAttribute('aria-hidden', 'false');
            document.body.classList.add('body-scroll-locked');
            titleEl.textContent = 'Loading...';
            uuidEl.textContent = uuid ? `UUID: ${uuid}` : '';
            bodyEl.innerHTML = `<div class="article-modal-loading"><i class="bi bi-arrow-repeat article-spinner"></i> Loading...</div>`;
            openNewBtn.onclick = () => window.open(pageUrl, '_blank', 'noopener');

            lastFocus = document.activeElement;
            closeBtn.focus();

            // [修复] History 逻辑：只在不同路径时 Push
            if (location.pathname !== pageUrl) {
                history.pushState({ modal: 'article', url: pageUrl }, '', pageUrl);
                pushedState = true;
            }
            isOpen = true;

            try {
                const resp = await fetch(`/api/intelligence/${encodeURIComponent(uuid)}`);
                if (resp.status === 401) {
                    bodyEl.innerHTML = `<div style="color:#c00;">You are not authorized.</div>`;
                    return;
                }
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const payload = await resp.json();
                const article = payload?.data || payload;

                titleEl.textContent = article?.EVENT_TITLE || titleFallback || 'Detail';

                // 调用统一渲染器
                bodyEl.innerHTML = ArticleDetailRenderer.generateHTML(article);

                // 绑定评分事件，传入 Modal 专用的 Toast 容器 ID
                ArticleDetailRenderer.bindEvents(bodyEl, uuid, 'article-toast-container');

                // Modal 内部链接跳转拦截
                bodyEl.querySelectorAll('a[href^="/intelligence/"]').forEach(a => {
                    a.addEventListener('click', (e) => {
                        // 如果是类似 Find Similar 这种在新窗口打开的，不拦截
                        if (a.target === '_blank' || e.button !== 0 || e.metaKey || e.ctrlKey) return;
                        e.preventDefault();
                        const nextUuid = (a.href.match(/\/intelligence\/([^/?#]+)/i) || [,''])[1];
                        if (nextUuid) openByUuid(nextUuid);
                    });
                });

                copyBtn.onclick = async () => {
                    try {
                        await navigator.clipboard.writeText(location.origin + pageUrl);
                        ArticleDetailRenderer.showToast('article-toast-container', 'Link copied!', 'success');
                    } catch {
                        ArticleDetailRenderer.showToast('article-toast-container', 'Copy failed', 'danger');
                    }
                };
            } catch (err) {
                titleEl.textContent = 'Load Failed';
                bodyEl.innerHTML = `<div style="color:#c00;">Failed to load: ${String(err)}</div>`;
            }
        }

        // 修改 1：在 close 函数里打标记
        function close({ fromHistory } = { fromHistory: false }) {
            if (!isOpen) return;
            overlay.style.display = 'none';
            overlay.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('body-scroll-locked');
            bodyEl.innerHTML = '';
            isOpen = false;

            if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();

            if (!fromHistory && pushedState) {
                // [新增] 告诉外层列表：这次是我主动调用的后退，你不要刷新！
                window._preventListReload = true;
                history.back();
            }
            pushedState = false;
        }

        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        closeBtn.addEventListener('click', () => close());
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen) close(); });

        document.addEventListener('click', async (e) => {
            const a = e.target.closest('a.article-title[data-uuid]');
            if (!a) return;
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            const uuid = a.dataset.uuid;
            if (uuid) await open(`/intelligence/${uuid}`, uuid, a.textContent?.trim() || 'Detail');
        });

        // 修改 2：在底部的 popstate 里打标记（应对用户点击浏览器左上角的“后退”按钮）
        window.addEventListener('popstate', () => {
            if (isOpen) {
                // [新增] 告诉外层列表：用户按了浏览器后退键，我来负责关弹窗，你不要刷新！
                window._preventListReload = true;
                close({ fromHistory: true });
            }
        });
    })();
});
