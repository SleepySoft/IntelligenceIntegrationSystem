/* static/js/article_detail_renderer.js */

window.ArticleDetailRenderer = {
    // 基础防 XSS 转义（正文需要渲染 HTML 时不使用这个）
    escapeHTML: function(str) {
        if (!str) return '';
        return String(str).replace(/[&<>"']/g, function(m) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
        });
    },

    anyTimeToTimeStr: function(s) {
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
    },

    renderStars: function(score) {
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
    },

    renderRatingTable: function(article) {
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
                    <td>${this.escapeHTML(key)}</td>
                    <td>${this.renderStars(score)}</td>
                    <td>
                        <input id="rating-${this.escapeHTML(key)}" type="number" class="form-control form-control-sm"
                            value="${manual}" min="0" max="10" step="0.5" style="width: 80px;"
                            oninput="if(this.value<0)this.value=0;if(this.value>10)this.value=10;">
                    </td>
                </tr>`;
        });

        return `
            <div class="mt-4">
                <h5><i class="bi bi-graph-up"></i> Analysis & Evaluation</h5>
                <div class="table-responsive">
                    <table class="table table-sm">
                        <thead><tr><th>Dimension</th><th>Rating</th><th>Manual Rating</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="mt-3">
                    <button id="submit-rating" class="btn btn-primary btn-sm">
                        <i class="bi bi-check-circle"></i> Submit Manual Ratings
                    </button>
                </div>
            </div>`;
    },

    // 统一生成 HTML 结构
    generateHTML: function(article) {
        const uuid = this.escapeHTML(article?.UUID || '');
        const informant = article?.INFORMANT ? this.escapeHTML(article.INFORMANT) : '';
        const pubTime = this.anyTimeToTimeStr(article?.PUB_TIME || 'N/A');
        const title = this.escapeHTML(article?.EVENT_TITLE || 'No Title');
        const brief = this.escapeHTML(article?.EVENT_BRIEF || 'No Brief');

        // 正文保持原文渲染，不做强制转义以支持排版。若需纯文本展示请加上 this.escapeHTML()
        const content = article?.EVENT_TEXT || 'No Content';

        const locations = Array.isArray(article?.LOCATION) ? article.LOCATION.map(i => this.escapeHTML(i)) : [];
        const people = Array.isArray(article?.PEOPLE) ? article.PEOPLE.map(i => this.escapeHTML(i)) : [];
        const orgs = Array.isArray(article?.ORGANIZATION) ? article.ORGANIZATION.map(i => this.escapeHTML(i)) : [];
        const times = Array.isArray(article?.TIME) ? article.TIME.map(this.anyTimeToTimeStr) : [];
        const impact = this.escapeHTML(article?.IMPACT || 'No Impact');
        const tips = this.escapeHTML(article?.TIPS || 'No Tips');

        // [修复] 补全了 <a> 标签
        return `
        <div class="article-preview" id="article-root-${uuid}" data-uuid="${uuid}">
            <section class="header-box key-points">
                <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between;">
                    <div style="min-width:0;">
                        <div style="font-size:12px; color:#555;">
                            <i class="bi bi-calendar-event"></i> ${pubTime}
                            &nbsp;|&nbsp; <i class="bi bi-upc-scan"></i> ${uuid}
                        </div>
                        <h3 style="margin:6px 0 0; font-size:20px; line-height:1.35;">${title}</h3>
                        <div style="margin-top:6px; color:#333;">${brief}</div>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center;">
                        ${informant ? `<a href="${informant}" target="_blank" class="btn btn-outline-secondary btn-sm"><i class="bi bi-link-45deg"></i> Source</a>` : ''}
                        <a href="/intelligences?search_mode=vector_similar&reference=${uuid}&score_threshold=0.6" class="btn btn-outline-primary btn-sm">
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

            <section class="meta-card" style="margin-top:10px;">
                <h5><i class="bi bi-clock-history"></i> Event Time(s)</h5>
                ${times.length ? times.join(', ') : 'No specific timing data'}
            </section>

            <section class="content-section" style="margin-top:16px;">
                ${content}
            </section>

            <section style="margin-top:16px;">
                ${this.renderRatingTable(article)}
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
    },

    // 绑定评分提交逻辑
    bindEvents: function(containerElement, uuid, toastContainerId) {
        const submitBtn = containerElement.querySelector('#submit-rating');
        if (!submitBtn) return;

        submitBtn.addEventListener('click', async () => {
            const ratings = {};
            containerElement.querySelectorAll('input[id^="rating-"]').forEach(input => {
                const dim = input.id.replace('rating-', '');
                ratings[dim] = input.value;
            });

            try {
                const r = await fetch('/manual_rate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        uuid: uuid,
                        ratings: ratings,
                        timestamp: new Date().toISOString()
                    })
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                this.showToast(toastContainerId, 'Ratings submitted successfully!', 'success');
            } catch (e) {
                this.showToast(toastContainerId, 'Error submitting ratings: ' + (e.message || e), 'danger');
            }
        });
    },

    showToast: function(containerId, message, type) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `article-toast ${type}`;
        toast.innerHTML = `<div style="flex:1;">${message}</div><button class="toast-close"><i class="bi bi-x-lg"></i></button>`;
        container.appendChild(toast);
        const remove = () => toast.remove();
        toast.querySelector('.toast-close').addEventListener('click', remove);
        setTimeout(remove, 2400);
    }
};
