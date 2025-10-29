/**
 * * 前端应用主脚本 (app.js)
 * */

// --- 1. 全局状态和常量 ---

// 存储当前查询状态，用于分页
let currentQueryState = {
    page: 1,
    per_page: 10,
    search_mode: document.querySelector('#search-mode-tabs .nav-link.active').dataset.mode
};

// 媒体来源数据库 (来自 article_source_enhancer_script)
const mediaSources = [
    // ... (你提供的长列表，此处省略以保持简洁) ...
    { domain: "wsj.com", nameCN: "华尔街日报", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "nytimes.com", nameCN: "纽约时报", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    // ... 确保你已将所有媒体源复制到此处
    { domain: "eltiempo.com", nameCN: "时代报", country: "Colombia", flag: "🇨🇴", accessibleInChina: true },
];


// --- 2. 核心DOM元素 ---

const searchForm = document.getElementById('search-form');
const searchButton = document.getElementById('search-button');
const spinner = searchButton.querySelector('.spinner-border');
const resultsContainer = document.getElementById('results-container');
const articleListContent = document.getElementById('article-list-content');
const paginationContainer = document.getElementById('pagination-container');
const resultsCountEl = document.getElementById('results-count');
const resultsTotalEl = document.getElementById('results-total');
const searchModeTabs = document.querySelectorAll('#search-mode-tabs .nav-link');


// --- 3. 核心功能：API
/**
 * 异步获取查询结果
 * @param {object} queryParams - 完整的查询参数
 */
async function fetchResults(queryParams) {
    // A. 准备 UI
    searchButton.disabled = true;
    spinner.style.display = 'inline-block';

    try {
        // B. 调用后端 API
        const response = await fetch('/intelligences/query', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(queryParams),
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Network response was not ok');
        }

        // 假设后端返回: { results: [...], total: XXX }
        // 注意：我们不再接收HTML，而是接收JSON
        const data = await response.json();

        // C. 渲染结果
        renderArticles(data.results);
        renderPagination(
            data.total,
            queryParams.page,
            queryParams.per_page
        );

        // D. 更新统计数据
        resultsCountEl.textContent = data.results.length;
        resultsTotalEl.textContent = data.total;
        resultsContainer.style.display = 'block';

        // E. 运行后处理脚本 (关键!)
        // 因为内容是动态添加的，必须手动调用这些函数
        updateTimeBackgrounds();
        enhanceSourceLinks();

    } catch (error) {
        console.error('Fetch error:', error);
        articleListContent.innerHTML = `<div class="alert alert-danger" role="alert">Query Error: ${error.message}</div>`;
        resultsContainer.style.display = 'block';
        resultsTotalEl.textContent = 0;
        resultsCountEl.textContent = 0;
    } finally {
        // F. 恢复 UI
        searchButton.disabled = false;
        spinner.style.display = 'none';
    }
}


// --- 4. 核心功能：渲染
/**
 * [JS实现] 对应 Python 的 generate_articles_table
 * @param {Array<object>} articles - 从API获取的文章对象数组
 */
function renderArticles(articles) {
    if (!articles || articles.length === 0) {
        articleListContent.innerHTML = '<p class="text-center text-muted">No results found.</p>';
        return;
    }

    const articlesHTML = articles.map(article => {
        const uuid = escapeHTML(article.UUID);
        const informant = escapeHTML(article.INFORMANT || "");
        const intelUrl = `/intelligence/${uuid}`;

        const informant_html = isValidUrl(informant)
            ? `<a href="${informant}" target="_blank" class="source-link">${informant}</a>`
            : (informant || 'Unknown Source');

        const appendix = article.APPENDIX || {};
        const archived_time = escapeHTML(appendix.APPENDIX_TIME_ARCHIVED || '');
        const max_rate_class = escapeHTML(appendix.APPENDIX_MAX_RATE_CLASS || '');
        const max_rate_score = appendix.APPENDIX_MAX_RATE_SCORE;

        let max_rate_display = "";
        if (max_rate_class && max_rate_score !== null && max_rate_score !== undefined) {
            max_rate_display = `
            <div class="article-rating mt-2">
                ${max_rate_class}：
                ${createRatingStars(max_rate_score)}
            </div>`;
        }

        let archived_html = "";
        if (archived_time) {
            archived_html = `
            <span class="article-time archived-time" data-archived="${archived_time}">
                Archived: ${archived_time}
            </span>`;
        }

        return `
        <div class="article-card">
            <h3>
                <a href="${intelUrl}" target="_blank" class="article-title">
                    ${escapeHTML(article.EVENT_TITLE || "No Title")}
                </a>
            </h3>
            <div class="article-meta">
                ${archived_html}
                <span class="article-time">Publish: ${escapeHTML(article.PUB_TIME || 'No Datetime')}</span>
                <span class="article-source">Source: ${informant_html}</span>
            </div>
            <p class="article-summary">${escapeHTML(article.EVENT_BRIEF || "No Brief")}</p>
            <div class="debug-info">
                ${max_rate_display}
                <span class="debug-label">UUID:</span> ${uuid}
            </div>
        </div>`;
    }).join('');

    articleListContent.innerHTML = articlesHTML;
}

/**
 * [JS实现] 对应 Python 的分页逻辑
 */
function renderPagination(total_results, current_page, per_page) {
    const total_pages = Math.max(1, Math.ceil(total_results / per_page));
    current_page = Number(current_page);

    let paginationHTML = '<ul class="pagination justify-content-center">';

    // Previous Button
    const prevDisabled = current_page === 1 ? "disabled" : "";
    paginationHTML += `
    <li class="page-item ${prevDisabled}">
        <a class="page-link" href="#" data-page="${current_page - 1}">Previous</a>
    </li>`;

    // Page Numbers (只显示10页)
    const maxPagesToShow = 10;
    let startPage = Math.max(1, current_page - Math.floor(maxPagesToShow / 2));
    let endPage = Math.min(total_pages, startPage + maxPagesToShow - 1);

    if (endPage - startPage + 1 < maxPagesToShow) {
        startPage = Math.max(1, endPage - maxPagesToShow + 1);
    }

    for (let i = startPage; i <= endPage; i++) {
        const active = i === current_page ? "active" : "";
        paginationHTML += `
        <li class="page-item ${active}">
            <a class="page-link" href="#" data-page="${i}">${i}</a>
        </li>`;
    }

    // Next Button
    const nextDisabled = current_page >= total_pages ? "disabled" : "";
    paginationHTML += `
    <li class="page-item ${nextDisabled}">
        <a class="page-link" href="#" data-page="${current_page + 1}">Next</a>
    </li>`;

    paginationHTML += '</ul>';
    paginationContainer.innerHTML = paginationHTML;
}


// --- 5. 辅助函数 (来自你提供的脚本) ---

/**
 * [辅助] XSS 防护
 */
function escapeHTML(str) {
    if (str === null || str === undefined) return "";
    return String(str).replace(/[&<>"']/g, function(m) {
        return {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[m];
    });
}

/**
 * [辅助] 检查 URL
 */
function isValidUrl(url) {
    if (!url) return false;
    return url.match(/^(https?|ftp):\/\//) !== null;
}

/**
 * [辅助] [JS实现] 对应 Python 的 create_rating_stars
 */
function createRatingStars(score) {
    if (typeof score !== 'number' || score < 0 || score > 10) {
        return "";
    }

    let stars = "";
    let full_stars = Math.floor(score / 2);
    let half_star = (score % 2 >= 1);
    let empty_stars = 5 - full_stars - (half_star ? 1 : 0);

    for(let i=0; i<full_stars; i++) stars += '<i class="bi bi-star-fill text-warning"></i> ';
    if(half_star) stars += '<i class="bi bi-star-half text-warning"></i> ';
    for(let i=0; i<empty_stars; i++) stars += '<i class="bi bi-star text-warning"></i> ';

    stars += ` <span class="ms-2 text-muted">${score.toFixed(1)}/10</span>`;
    return stars;
}

/**
 * [共享脚本] 对应 article_table_color_gradient_script
 */
function updateTimeBackgrounds() {
    const now = new Date().getTime();
    const twelveHours = 12 * 60 * 60 * 1000;

    document.querySelectorAll('.archived-time').forEach(el => {
        const archivedTime = new Date(el.dataset.archived).getTime();
        if (isNaN(archivedTime)) return;

        const timeDiff = now - archivedTime;
        let ratio = Math.min(1, Math.max(0, timeDiff / twelveHours));

        const r = Math.round(255 - ratio * (255 - 227));
        const g = Math.round(165 - ratio * (165 - 242));
        const b = Math.round(0 - ratio * (0 - 253));

        el.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
    });
}

/**
 * [共享脚本] 对应 article_source_enhancer_script
 */
function enhanceSourceLinks() {
    function findSourceInfo(hostname) {
        let source = mediaSources.find(s => s.domain === hostname);
        if (source) return source;
        source = mediaSources.find(s => hostname.endsWith('.' + s.domain));
        return source || null;
    }

    function getHighlightDomain(hostname) {
        const complexTldMatch = hostname.match(/[^.]+\.(?:co|com|net|org|gov|edu)\.[^.]+$/);
        if (complexTldMatch) return complexTldMatch[0];
        const simpleTldMatch = hostname.match(/[^.]+\.[^.]+$/);
        return simpleTldMatch ? simpleTldMatch[0] : hostname;
    }

    document.querySelectorAll('.article-source').forEach(sourceElement => {
        const link = sourceElement.querySelector('a.source-link');
        if (!link || !link.href) return;

        try {
            const url = new URL(link.href);
            const hostname = url.hostname;
            const sourceInfo = findSourceInfo(hostname);

            const container = document.createElement('div');
            container.className = 'source-link-container';

            const prefixSpan = document.createElement('span');
            prefixSpan.className = 'source-prefix';

            if (sourceInfo) {
                const accessibilityIcon = sourceInfo.accessibleInChina ? '✅' : '🚫';
                prefixSpan.textContent = ` ${accessibilityIcon} ${sourceInfo.flag}`;
            } else {
                prefixSpan.textContent = ' ❔  🌍';
            }

            const highlightPart = getHighlightDomain(hostname);
            const originalText = link.textContent;
            if (originalText && originalText.includes(highlightPart)) {
                link.innerHTML = originalText.replace(
                    highlightPart,
                    `<span class="domain-highlight">${highlightPart}</span>`
                );
            }

            // 确保 DOM 结构正确
            if (link.parentNode === sourceElement) {
                container.appendChild(prefixSpan);
                container.appendChild(link); // link 会被自动从原位置移动

                const sourceTextNode = sourceElement.firstChild; // "Source: "
                sourceElement.innerHTML = '';
                sourceElement.appendChild(sourceTextNode);
                sourceElement.appendChild(container);
            }

        } catch (e) {
            console.error('Error processing source link:', e);
        }
    });
}


// --- 6. 事件监听器 ---

document.addEventListener('DOMContentLoaded', () => {

    // 监听表单提交
    searchForm.addEventListener('submit', (e) => {
        e.preventDefault(); // 阻止表单默认提交

        // 1. 从表单收集数据
        const formData = new FormData(searchForm);
        const params = Object.fromEntries(formData.entries());

        // 2. 构建与 active tab 一致的 payload

        // 公共分页字段
        const payload = {
          page: 1,
          per_page: Number(params.per_page) || 10,
          search_mode: document.querySelector('#search-mode-tabs .nav-link.active').dataset.mode
        };

        if (payload.search_mode === 'vector') {
          payload.keywords                = params.keywords || '';
          payload.score_threshold         = Number(params.score_threshold) || 0.5;
          payload.in_summary              = document.getElementById('in_summary').checked;
          payload.in_fulltext             = document.getElementById('in_fulltext').checked;
        } else {   // mongo
          if (params.start_time)    payload.start_time    = params.start_time;
          if (params.end_time)      payload.end_time      = params.end_time;
          if (params.locations)     payload.locations     = params.locations;
          if (params.peoples)       payload.peoples       = params.peoples;
          if (params.organizations) payload.organizations = params.organizations;
        }

        // 3. 更新全局状态并发起请求
        currentQueryState = { ...payload };
        fetchResults(payload);
    });

    // 监听分页点击 (事件委托)
    paginationContainer.addEventListener('click', (e) => {
        e.preventDefault();
        const target = e.target.closest('.page-link');

        if (target && !target.closest('.page-item.disabled')) {
            const newPage = Number(target.dataset.page);
            if (newPage && newPage !== currentQueryState.page) {
                currentQueryState.page = newPage;
                fetchResults(currentQueryState); // 使用当前状态重新获取
            }
        }
    });

    // 定时更新时间背景 (来自共享脚本)
    // 立即执行一次
    updateTimeBackgrounds();
    // 然后定时执行
    setInterval(updateTimeBackgrounds, 60000);
});