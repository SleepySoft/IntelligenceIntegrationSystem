document.addEventListener('DOMContentLoaded', () => {
    // 复用 ArticleRenderer 提供的时间高亮和卡片生成能力
    // 这里第二个参数传空，因为聚类页面我们不使用标准的分页条
    const renderer = new ArticleRenderer('article-list-container', '');
    const listContainer = document.getElementById('article-list-container');
    const limitSelect = document.getElementById('limit-select');
    const refreshBtn = document.getElementById('refresh-btn');

    if (window.ArticleModalManager) {
        ArticleModalManager.init({
            history: false
        });
    }

    async function loadClusters(forceRefresh = false) {
        const limit = limitSelect ? limitSelect.value : 50;
        renderer.showLoading();

        try {
            const refreshParam = forceRefresh ? '&refresh=1' : '';
            const response = await fetch(`/api/clusters/latest?source=online&sort_by=time&desc=1&limit=${limit}${refreshParam}`);
            if (!response.ok) throw new Error(`API Error: ${response.status}`);

            const data = await response.json();
            const clusters = data.clusters || [];

            if (clusters.length === 0) {
                listContainer.innerHTML = `<p style="text-align:center; padding: 50px;">${IIS_I18N.t('no_clusters')}</p>`;
                return;
            }

            // 生成聚类 HTML
            let html = '';
            clusters.forEach(cluster => {
                const doc = cluster.repr_doc;
                // 利用重构后的 generateArticleCardHtml 单独生成代表文章的卡片
                const reprCardHtml = renderer.generateArticleCardHtml(doc);

                // 只有 size > 1 时才显示展开按钮

                const hasToggle = cluster.size > 1;

                const toggleBtn = hasToggle
                    ? `<button class="cluster-toggle-btn"
                               data-cluster-id="${cluster.cluster_id}"
                               data-related-count="${cluster.size - 1}">
                         <i class="bi bi-chevron-down"></i> ${IIS_I18N.t('expand_related', {count: cluster.size - 1})}
                       </button>`
                    : '';

                html += `
                <div class="cluster-container" data-cluster-id="${cluster.cluster_id}">
                    <div class="cluster-badge">
                        <i class="bi bi-diagram-3"></i> ${IIS_I18N.t('cluster_id')} <span class="notranslate" translate="no">${renderer.escapeHTML(cluster.cluster_id)}</span> · ${IIS_I18N.t('reports_count', {count: cluster.size})}
                    </div>
                    <div class="cluster-header ${hasToggle ? 'has-toggle' : ''}">
                        ${toggleBtn}
                        ${reprCardHtml}
                    </div>
                    <div class="cluster-members" id="members-${cluster.cluster_id}"></div>
                </div>`;
            });

            listContainer.innerHTML = html;

            // 触发来源图标渲染和时间颜色更新
            renderer.enhanceSourceLinks();
            renderer.updateTimeBackgrounds();

        } catch (error) {
            console.error('Load Error:', error);
            renderer.showError(error.message);
        }
    }

    // 处理展开/收起事件 (事件委托)
    listContainer.addEventListener('click', async (e) => {
        const btn = e.target.closest('.cluster-toggle-btn, .cluster-inline-collapse-btn');
        if (!btn) return;

        const clusterId = btn.getAttribute('data-cluster-id');
        const membersDiv = document.getElementById(`members-${clusterId}`);
        const containerEl = btn.closest('.cluster-container');
        const topBtn = containerEl ? containerEl.querySelector('.cluster-toggle-btn[data-cluster-id]') : null;
        const relatedCount = topBtn ? topBtn.getAttribute('data-related-count') : '';

        if (!membersDiv || !containerEl) return;

        const isInlineCollapse = btn.classList.contains('cluster-inline-collapse-btn');

        // =========================
        // 收起：顶部按钮再次点击，或者点击任意子项后的 Collapse
        // =========================
        if (membersDiv.classList.contains('expanded') && (isInlineCollapse || btn === topBtn)) {
            membersDiv.classList.remove('expanded');

            if (topBtn) {
                topBtn.innerHTML = `<i class="bi bi-chevron-down"></i> ${IIS_I18N.t('expand_related', {count: relatedCount})}`;
            }
            return;
        }

        // 如果点的是子项里的 Collapse，但当前没展开，就不用处理
        if (isInlineCollapse) return;

        // =========================
        // 展开
        // =========================
        membersDiv.classList.add('expanded');

        if (topBtn) {
            topBtn.innerHTML = `<i class="bi bi-chevron-up"></i> ${IIS_I18N.t('collapse')}`;
        }

        // 如果已经加载过，直接返回
        if (membersDiv.innerHTML.trim() !== '') return;

        membersDiv.innerHTML = `<div class="loading-spinner"><i class="bi bi-arrow-repeat article-spinner"></i> ${IIS_I18N.t('loading_members')}</div>`;

        try {
            const resp = await fetch(`/api/clusters/${clusterId}/members?source=online&sort_by=time&desc=1&limit=500`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            const data = await resp.json();
            const items = data.items || [];

            const reprTitleEl = containerEl.querySelector('.article-title');
            const reprUuid = reprTitleEl ? reprTitleEl.getAttribute('data-uuid') : null;

            const filteredItems = items.filter(item => item.uuid !== reprUuid);

            if (filteredItems.length === 0) {
                membersDiv.innerHTML = `<p style="color:#666; font-size: 0.9em;">${IIS_I18N.t('no_other_members')}</p>`;
                return;
            }

            const membersHtml = filteredItems.map(item => `
                <div class="cluster-member-item">
                    ${renderer.generateArticleCardHtml(item.doc)}
                    <div class="cluster-member-actions">
                        <button class="cluster-inline-collapse-btn" data-cluster-id="${clusterId}">
                            <i class="bi bi-chevron-up"></i> ${IIS_I18N.t('collapse')}
                        </button>
                    </div>
                </div>
            `).join('');

            membersDiv.innerHTML = membersHtml;

            renderer.enhanceSourceLinks();
            renderer.updateTimeBackgrounds();

        } catch (err) {
            membersDiv.innerHTML = `<div style="color:red;">${IIS_I18N.t('member_load_failed', {message: err.message})}</div>`;
        }
    });

    if (refreshBtn) refreshBtn.addEventListener('click', () => loadClusters(true));
    if (limitSelect) limitSelect.addEventListener('change', loadClusters);

    // 初始加载
    loadClusters();
});
