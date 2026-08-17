/* static/js/article_modal_manager.js */

(function () {
    if (window.ArticleModalManager) return;

    const DEFAULT_OPTIONS = {
        apiBase: (window.IIS_BASE_PATH || '') + '/api/intelligence',
        pageBase: (window.IIS_BASE_PATH || '') + '/intelligence',
        titleSelector: 'a.article-title[data-uuid]',
        toastContainerId: 'article-toast-container',
        history: false,              // true: pushState + back close
        autoInjectMarkup: true,
        debug: false
    };

    const state = {
        initialized: false,
        options: { ...DEFAULT_OPTIONS },

        overlay: null,
        bodyEl: null,
        titleEl: null,
        uuidEl: null,
        closeBtn: null,
        copyBtn: null,
        openNewBtn: null,

        isOpen: false,
        pushedState: false,
        lastFocus: null,
        currentUuid: '',
        currentPageUrl: ''
    };

    function log(...args) {
        if (state.options.debug) {
            console.log('[ArticleModalManager]', ...args);
        }
    }

    function ensureMarkup() {
        if (document.getElementById('article-detail-overlay')) {
            bindDomRefs();
            return;
        }

        if (!state.options.autoInjectMarkup) {
            log('Modal markup not found and autoInjectMarkup=false');
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.innerHTML = `
        <div id="article-detail-overlay" class="article-modal-overlay" aria-hidden="true" style="display:none;">
          <div class="article-modal" role="dialog" aria-modal="true" aria-labelledby="article-modal-title">
            <div class="article-modal-header">
              <div class="header-left">
                <div class="uuid" id="article-modal-uuid"></div>
                <h2 id="article-modal-title" class="article-modal-title">Loading...</h2>
              </div>
              <div class="article-modal-actions">
                <button type="button" id="article-copy-link-btn" class="article-modal-btn" title="复制链接">
                  <i class="bi bi-link-45deg"></i>
                </button>
                <button type="button" id="article-open-newtab-btn" class="article-modal-btn" title="在新标签打开">
                  <i class="bi bi-box-arrow-up-right"></i>
                </button>
                <button type="button" id="article-close-btn" class="article-modal-btn" title="关闭">
                  <i class="bi bi-x-lg"></i>
                </button>
              </div>
            </div>

            <div class="article-modal-body" id="article-modal-body">
              <div class="article-modal-loading">
                <i class="bi bi-arrow-repeat article-spinner"></i> Loading...
              </div>
            </div>

            <div id="article-toast-container" class="article-toast-container"></div>
          </div>
        </div>`;
        document.body.appendChild(wrapper.firstElementChild);

        bindDomRefs();
    }

    function bindDomRefs() {
        state.overlay = document.getElementById('article-detail-overlay');
        state.bodyEl = document.getElementById('article-modal-body');
        state.titleEl = document.getElementById('article-modal-title');
        state.uuidEl = document.getElementById('article-modal-uuid');
        state.closeBtn = document.getElementById('article-close-btn');
        state.copyBtn = document.getElementById('article-copy-link-btn');
        state.openNewBtn = document.getElementById('article-open-newtab-btn');
    }

    function setLoading(uuid) {
        if (!state.overlay || !state.bodyEl || !state.titleEl || !state.uuidEl) return;
        state.overlay.style.display = 'flex';
        state.overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('body-scroll-locked');

        state.titleEl.textContent = 'Loading...';
        state.uuidEl.textContent = uuid ? `UUID: ${uuid}` : '';
        state.bodyEl.innerHTML = `
            <div class="article-modal-loading">
                <i class="bi bi-arrow-repeat article-spinner"></i> Loading...
            </div>`;
    }

    async function fetchArticle(uuid) {
        const url = `${state.options.apiBase}/${encodeURIComponent(uuid)}`;
        const resp = await fetch(url);

        if (resp.status === 401) {
            throw new Error('You are not authorized.');
        }
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }

        const payload = await resp.json();
        return payload?.data || payload;
    }

    function bindModalInnerArticleLinks() {
        if (!state.bodyEl) return;

        state.bodyEl.querySelectorAll(`a[href^="${state.options.pageBase}/"]`).forEach(a => {
            a.addEventListener('click', async (e) => {
                if (a.target === '_blank' || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
                    return;
                }

                e.preventDefault();

                const match = a.getAttribute('href').match(/\/intelligence\/([^/?#]+)/i);
                const nextUuid = match ? match[1] : '';
                if (nextUuid) {
                    await openByUuid(nextUuid, { updateHistory: state.options.history });
                }
            });
        });
    }

    function bindCopyButton(pageUrl) {
        if (!state.copyBtn) return;

        state.copyBtn.onclick = async () => {
            try {
                await navigator.clipboard.writeText(location.origin + pageUrl);
                if (window.ArticleDetailRenderer?.showToast) {
                    window.ArticleDetailRenderer.showToast(
                        state.options.toastContainerId,
                        'Link copied!',
                        'success'
                    );
                }
            } catch {
                if (window.ArticleDetailRenderer?.showToast) {
                    window.ArticleDetailRenderer.showToast(
                        state.options.toastContainerId,
                        'Copy failed',
                        'danger'
                    );
                }
            }
        };
    }

    function bindOpenNewTabButton(pageUrl) {
        if (!state.openNewBtn) return;
        state.openNewBtn.onclick = () => window.open(pageUrl, '_blank', 'noopener');
    }

    async function open(pageUrl, uuid, titleFallback, opts = {}) {
        const updateHistory = opts.updateHistory ?? state.options.history;

        if (!window.ArticleDetailRenderer) {
            console.error('[ArticleModalManager] ArticleDetailRenderer is required.');
            return;
        }

        ensureMarkup();
        if (!state.overlay) {
            console.error('[ArticleModalManager] Modal markup not found.');
            return;
        }

        state.lastFocus = document.activeElement;
        state.currentUuid = uuid || '';
        state.currentPageUrl = pageUrl || `${state.options.pageBase}/${encodeURIComponent(uuid || '')}`;

        setLoading(uuid);
        bindOpenNewTabButton(state.currentPageUrl);

        if (state.closeBtn) {
            state.closeBtn.focus();
        }

        if (updateHistory && location.pathname !== state.currentPageUrl) {
            history.pushState({ modal: 'article', url: state.currentPageUrl }, '', state.currentPageUrl);
            state.pushedState = true;
        } else {
            state.pushedState = false;
        }

        state.isOpen = true;

        try {
            const article = await fetchArticle(uuid);

            state.titleEl.textContent = article?.EVENT_TITLE || titleFallback || 'Detail';
            state.bodyEl.innerHTML = window.ArticleDetailRenderer.generateHTML(article);
            window.ArticleDetailRenderer.bindEvents(state.bodyEl, uuid, state.options.toastContainerId);

            bindCopyButton(state.currentPageUrl);
            bindModalInnerArticleLinks();
        } catch (err) {
            state.titleEl.textContent = 'Load Failed';
            state.bodyEl.innerHTML = `<div style="color:#c00;">Failed to load: ${String(err.message || err)}</div>`;
        }
    }

    async function openByUuid(uuid, opts = {}) {
        if (!uuid) return;
        const pageUrl = `${state.options.pageBase}/${encodeURIComponent(uuid)}`;
        await open(pageUrl, uuid, 'Detail', opts);
    }

    function close({ fromHistory = false } = {}) {
        if (!state.isOpen || !state.overlay) return;

        state.overlay.style.display = 'none';
        state.overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('body-scroll-locked');

        if (state.bodyEl) state.bodyEl.innerHTML = '';
        state.isOpen = false;

        if (state.lastFocus && typeof state.lastFocus.focus === 'function') {
            state.lastFocus.focus();
        }

        if (!fromHistory && state.pushedState) {
            // 兼容你当前列表页的逻辑
            window._preventListReload = true;
            history.back();
        }

        state.pushedState = false;
    }

    function onDocumentClick(e) {
        const a = e.target.closest(state.options.titleSelector);
        if (!a) return;

        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
            return;
        }

        e.preventDefault();

        const uuid = a.dataset.uuid;
        if (!uuid) return;

        // 插件导航委洺：先看插件是否声明 detailURL / onCardClick
        const plugin = (window.SubsystemUI && window.SubsystemUI.getPlugin(window.IIS_SUBSYSTEM || '')) || null;
        let nav = null;
        if (plugin) {
            const helpers = window.SubsystemUI.makeHelpers
                ? window.SubsystemUI.makeHelpers(null, window.IIS_BASE_PATH || '', window.IIS_SUBSYSTEM || '')
                : { base: window.IIS_BASE_PATH || '', subsystem: window.IIS_SUBSYSTEM || '' };
            nav = window.SubsystemUI.navFromPlugin(plugin, { UUID: uuid }, helpers);
        }

        if (nav) {
            if (nav.mode === 'navigate' && nav.url) {
                window.open(nav.url, '_self');           // 跳转自定义页
                return;
            }
            if (nav.mode === 'custom' && typeof plugin.onCardClick === 'function') {
                plugin.onCardClick({ UUID: uuid }, helpers, {
                    openModal: (doc, url) => open(url || `${state.options.pageBase}/${encodeURIComponent(doc?.UUID || uuid)}`, doc?.UUID || uuid, 'Detail', { updateHistory: state.options.history }),
                    navigate: (url, opts) => { if (opts && opts.newTab) window.open(url, '_blank', 'noopener'); else window.open(url, '_self'); },
                });
                return;
            }
        }

        open(`${state.options.pageBase}/${encodeURIComponent(uuid)}`, uuid, a.textContent?.trim() || 'Detail', {
            updateHistory: state.options.history
        });
    }

    function onOverlayClick(e) {
        if (e.target === state.overlay) {
            close();
        }
    }

    function onKeydown(e) {
        if (e.key === 'Escape' && state.isOpen) {
            close();
        }
    }

    function onPopState() {
        if (state.isOpen) {
            window._preventListReload = true;
            close({ fromHistory: true });
        }
    }

    function bindGlobalEvents() {
        document.addEventListener('click', onDocumentClick);
        document.addEventListener('keydown', onKeydown);
        window.addEventListener('popstate', onPopState);

        if (state.overlay) {
            state.overlay.addEventListener('click', onOverlayClick);
        }
        if (state.closeBtn) {
            state.closeBtn.addEventListener('click', () => close());
        }
    }

    function init(options = {}) {
        if (state.initialized) {
            log('already initialized');
            return window.ArticleModalManager;
        }

        state.options = { ...DEFAULT_OPTIONS, ...options };

        ensureMarkup();
        bindGlobalEvents();

        state.initialized = true;
        log('initialized with options:', state.options);

        return window.ArticleModalManager;
    }

    window.ArticleModalManager = {
        init,
        openByUuid,
        close,
        getState() {
            return {
                initialized: state.initialized,
                isOpen: state.isOpen,
                currentUuid: state.currentUuid,
                currentPageUrl: state.currentPageUrl,
                options: { ...state.options }
            };
        }
    };
})();
