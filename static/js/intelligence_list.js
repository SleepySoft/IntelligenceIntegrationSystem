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

    // 1) 校验：与详情页一致（0~10）
    window.validateRating = function(input) {
      const v = parseFloat(input.value);
      if (Number.isNaN(v)) return;
      if (v < 0) input.value = 0;
      else if (v > 10) input.value = 10;
    };

    // 2) Toast（Modal 内部展示）
    function showNotificationInModal(message, type /* 'success'|'danger' */) {
      const container = document.getElementById('article-toast-container');
      if (!container) return;
      const toast = document.createElement('div');
      toast.className = `article-toast ${type}`;
      toast.innerHTML = `
        <div style="flex:1;">${message}</div>
        <button class="toast-close" aria-label="Close"><i class="bi bi-x-lg"></i></button>`;
      container.appendChild(toast);
      const remove = () => { toast.remove(); };
      toast.querySelector('.toast-close').addEventListener('click', remove);
      setTimeout(remove, 2400);
    }

    // 3) 工具：星星渲染
    function renderStars(score) {
      const num = Number(score);
      if (!Number.isFinite(num)) return '';
      const full = Math.floor(num / 2);
      const half = (num % 2) >= 1;
      const empty = 5 - full - (half ? 1 : 0);
      let html = '<span class="stars">';
      for (let i = 0; i < full; i++) html += '<i class="bi bi-star-fill text-warning"></i> ';
      if (half) html += '<i class="bi bi-star-half text-warning"></i> ';
      for (let i = 0; i < empty; i++) html += '<i class="bi bi-star text-warning"></i> ';
      html += ` <span class="ms-2 text-muted">${num}/10</span></span>`;
      return html;
    }

    // 4) 工具：评分表渲染（保持 input id 规则）
    function renderRatingTable(article) {
      const rates = article?.RATE || {};
      const manualRatings = (article?.APPENDIX && (article.APPENDIX['__MANUAL_RATING__'] || article.APPENDIX['APPENDIX_MANUAL_RATING'])) || {};
      const keys = Object.keys(rates);
      if (!keys.length) return '';

      let rows = '';
      keys.forEach(key => {
        const score = rates[key];
        if (typeof score !== 'number' || score < 0 || score > 10) return;
        const manual = (manualRatings && manualRatings[key] != null) ? manualRatings[key] : score;
        rows += `
          <tr>
            <td>${key}</td>
            <td>${renderStars(score)}</td>
            <td>
              <input id="rating-${key}" type="number" class="form-control form-control-sm"
                value="${manual}" min="0" max="10" step="0.5" style="width: 80px;"
                oninput="validateRating(this)">
            </td>
          </tr>`;
      });

      return `
        <div class="mt-4">
          <h5><i class="bi bi-graph-up"></i> Analysis & Evaluation</h5>
          <div class="table-responsive">
            <table class="table table-sm">
              <thead>
                <tr><th>Dimension</th><th>Rating</th><th>Manual Rating</th></tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div class="mt-3">
            <button id="submit-rating" class="btn btn-primary btn-sm">
              <i class="bi bi-check-circle"></i> Submit Manual Ratings
            </button>
          </div>
        </div>`;
    }

    // 5) 工具：时间格式兜底
    function anyTimeToTimeStr(s) {
      try {
        const d = new Date(s);
        if (isNaN(d.getTime())) return s || 'N/A';
        const y = d.getFullYear();
        const m = String(d.getMonth()+1).padStart(2,'0');
        const day = String(d.getDate()).padStart(2,'0');
        const hh = String(d.getHours()).padStart(2,'0');
        const mm = String(d.getMinutes()).padStart(2,'0');
        const ss = String(d.getSeconds()).padStart(2,'0');
        return `${y}-${m}-${day} ${hh}:${mm}:${ss}`;
      } catch { return s || 'N/A'; }
    }

    // 6) 工具：详情整体渲染（从 JSON → HTML）
    function renderArticlePreviewHTML(article) {
      const uuid = article?.UUID || '';
      const informant = article?.INFORMANT || '';
      const pubTime = anyTimeToTimeStr(article?.PUB_TIME || 'N/A');
      const title = article?.EVENT_TITLE || 'No Title';
      const brief = article?.EVENT_BRIEF || 'No Brief';
      const content = article?.EVENT_TEXT || 'No Content';
      const locations = Array.isArray(article?.LOCATION) ? article.LOCATION : [];
      const people = Array.isArray(article?.PEOPLE) ? article.PEOPLE : [];
      const orgs = Array.isArray(article?.ORGANIZATION) ? article.ORGANIZATION : [];
      const times = Array.isArray(article?.TIME) ? article.TIME.map(anyTimeToTimeStr) : [];
      const impact = article?.IMPACT || 'No Impact';
      const tips = article?.TIPS || 'No Tips';

      return `
      <div class="article-preview" id="article-root" data-uuid="${uuid}">
        <section class="header-box key-points">
          <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between;">
            <div style="min-width:0;">
              <div style="font-size:12px; color:#555;">
                <i class="bi bi-calendar-event"></i> ${pubTime}
                &nbsp;|&nbsp; <i class="bi bi-upc-scan"></i> ${uuid}
              </div>
              <h3 style="margin:6px 0 0; font-size:20px; line-height:1.35;" data-article-title>${title}</h3>
              <div style="margin-top:6px; color:#333;">${brief}</div>
            </div>
            <div style="display:flex; gap:8px; align-items:center;">
              ${informant ? `${informant}
                  <i class="bi bi-link-45deg"></i> Source
                </a>` : ''}
              /intelligences?search_mode=vector_similar&reference=${uuid}&score_threshold=0.6
                <i class="bi bi-intersect"></i> Find Similar
              </a>
            </div>
          </div>
        </section>

        <section class="meta-grid">
          <div class="meta-card">
            <h5><i class="bi bi-geo-alt"></i> Geographic Locations</h5>
            ${locations.length ? locations.join(', ') : 'No location data'}
          </div>
          <div class="meta-card">
            <h5><i class="bi bi-people"></i> Related People</h5>
            ${people.length ? people.join(', ') : 'No associated people'}
          </div>
          <div class="meta-card">
            <h5><i class="bi bi-building"></i> Related Organizations</h5>
            ${orgs.length ? orgs.join(', ') : 'No related organizations'}
          </div>
        </section>

        <section class="meta-card">
          <h5><i class="bi bi-clock-history"></i> Event Time(s)</h5>
          ${times.length ? times.join(', ') : 'No specific timing data'}
        </section>

        <section class="content-section" style="margin-top:10px;">
          ${content}
        </section>

        <section style="margin-top:16px;">
          ${renderRatingTable(article)}
        </section>

        <section class="impact-card" style="margin-top:16px;">
          <h5><i class="bi bi-lightning-charge"></i> Potential Impact</h5>
          <p>${impact}</p>
        </section>

        <section class="tip-card" style="margin-top:12px;">
          <h5><i class="bi bi-lightbulb"></i> Analyst Notes</h5>
          <p>${tips}</p>
        </section>
      </div>`;
    }

    // 7) Modal 管理器：拦截点击→拉 JSON→渲染→水合→历史同步
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
      let currentUrl = null;

      async function openByUuid(uuid) {
        const pageUrl = `/intelligence/${encodeURIComponent(uuid)}`;
        await open(pageUrl, uuid, 'Detail');
      }

      async function open(pageUrl, uuid, titleFallback) {
        currentUrl = pageUrl;
        overlay.style.display = 'flex';
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('body-scroll-locked');
        titleEl.textContent = 'Loading...';
        uuidEl.textContent = uuid ? `UUID: ${uuid}` : '';
        bodyEl.innerHTML = `<div class="article-modal-loading">
          <i class="bi bi-arrow-repeat article-spinner"></i> Loading...
        </div>`;
        openNewBtn.onclick = () => window.open(pageUrl, '_blank', 'noopener');

        lastFocus = document.activeElement;
        closeBtn.focus();

        if (location.pathname + location.search !== pageUrl) {
          history.pushState({ modal: 'article', url: pageUrl }, '', pageUrl);
        }
        isOpen = true;

        try {
          const apiUrl = `/api/intelligence/${encodeURIComponent(uuid)}`;
          const resp = await fetch(apiUrl, { headers: { 'Accept': 'application/json' } });
          if (resp.status === 401) {
            titleEl.textContent = 'Unauthorized';
            bodyEl.innerHTML = `<div style="color:#c00;">You are not authorized.</div>`;
            return;
          }
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const payload = await resp.json();
          const article = payload?.data || payload;

          const finalTitle = article?.EVENT_TITLE || titleFallback || 'Detail';
          titleEl.textContent = finalTitle;

          bodyEl.innerHTML = renderArticlePreviewHTML(article);

          // 评分提交
          const rootEl = bodyEl.querySelector('#article-root');
          const submitBtn = rootEl?.querySelector('#submit-rating');
          if (submitBtn) {
            submitBtn.addEventListener('click', async () => {
              const ratings = {};
              rootEl.querySelectorAll('input[id^="rating-"]').forEach(input => {
                const dim = input.id.replace('rating-', '');
                ratings[dim] = input.value;
              });
              try {
                const r = await fetch('/manual_rate', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    uuid: article?.UUID || uuid,
                    ratings: ratings,
                    timestamp: new Date().toISOString()
                  })
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                showNotificationInModal('Ratings submitted successfully!', 'success');
              } catch (e) {
                showNotificationInModal('Error submitting ratings: ' + (e.message || e), 'danger');
              }
            });
          }

          // Modal 内部 “/intelligence/:uuid” 链接 → 继续在 Modal 打开
          bodyEl.querySelectorAll('a[href^="/intelligence/"]').forEach(a => {
            a.addEventListener('click', (e) => {
              if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              e.preventDefault();
              const nextUuid = (a.href.match(/\/intelligence\/([^/?#]+)/i) || [,''])[1];
              if (nextUuid) openByUuid(nextUuid);
            });
          });

          // 顶部工具按钮
          copyBtn.onclick = async () => {
            try {
              await navigator.clipboard.writeText(pageUrl);
              showNotificationInModal('链接已复制', 'success');
            } catch {
              showNotificationInModal('复制失败', 'danger');
            }
          };
          openNewBtn.onclick = () => window.open(pageUrl, '_blank', 'noopener');
        } catch (err) {
          titleEl.textContent = 'Load Failed';
          bodyEl.innerHTML = `<div style="color:#c00;">Failed to load: ${String(err)}</div>`;
        }
      }

      function close({ fromHistory } = { fromHistory: false }) {
        if (!isOpen) return;
        overlay.style.display = 'none';
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('body-scroll-locked');
        bodyEl.innerHTML = '';
        titleEl.textContent = 'Loading...';
        uuidEl.textContent = '';
        isOpen = false;

        if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();

        if (!fromHistory) {
          const listUrl = sessionStorage.getItem('list-last-url') || `${location.origin}/intelligences`;
          history.pushState({}, '', listUrl);
        }
      }

      overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
      closeBtn.addEventListener('click', () => close());
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen) close(); });

      // 列表标题点击拦截（左键无修饰）
      document.addEventListener('click', async (e) => {
        const a = e.target.closest('a.article-title[data-uuid]');
        if (!a) return;
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        sessionStorage.setItem('list-last-url', location.href);
        const uuid = a.dataset.uuid;
        if (uuid) await open(`/intelligence/${uuid}`, uuid, a.textContent?.trim() || 'Detail');
      });

      // popstate：地址栏切换与 Modal 状态同步
      window.addEventListener('popstate', () => {
        const m = location.pathname.match(/^\/intelligence\/([^/?#]+)/i);
        if (m) {
          const nextUuid = m[1];
          if (!isOpen) openByUuid(nextUuid);
        } else if (isOpen) {
          close({ fromHistory: true });
        }
      });

      // 处理用户“直达详情页”的情况（可选）
      if (/^\/intelligence\/[^/?#]+/i.test(location.pathname)) {
        const uuid = (location.pathname.match(/\/intelligence\/([^/?#]+)/i) || [,''])[1];
        if (uuid) openByUuid(uuid);
      }
    })();
});
