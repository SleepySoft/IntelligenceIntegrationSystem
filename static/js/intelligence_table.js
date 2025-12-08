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

// 媒体来源数据库
// domain: 用于匹配的关键域名
// nameCN: 网站中文名
// country: 所属国家/地区
// flag: 对应的 Emoji 国旗
// accessibleInChina: 在中国大陆是否可直接访问 (true: 是, false: 否)
const mediaSources = [
    // 美国 (USA)
    { domain: "wsj.com", nameCN: "华尔街日报", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "nytimes.com", nameCN: "纽约时报", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "voanews.com", nameCN: "美国之音", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "washingtonpost.com", nameCN: "华盛顿邮报", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "bloomberg.com", nameCN: "彭博社", country: "USA", flag: "🇺🇸", accessibleInChina: false },
    { domain: "cnn.com", nameCN: "美国有线电视新闻网", country: "USA", flag: "🇺🇸", accessibleInChina: false },

    // 英国 (UK)
    { domain: "bbc.com", nameCN: "英国广播公司", country: "UK", flag: "🇬🇧", accessibleInChina: false },
    { domain: "ft.com", nameCN: "金融时报", country: "UK", flag: "🇬🇧", accessibleInChina: false },
    { domain: "economist.com", nameCN: "经济学人", country: "UK", flag: "🇬🇧", accessibleInChina: false },
    { domain: "theguardian.com", nameCN: "卫报", country: "UK", flag: "🇬🇧", accessibleInChina: false },

    // 加拿大 (Canada)
    { domain: "rcinet.ca", nameCN: "加拿大国际广播电台", country: "Canada", flag: "🇨🇦", accessibleInChina: false },
    { domain: "cbc.ca", nameCN: "加拿大广播公司", country: "Canada", flag: "🇨🇦", accessibleInChina: false },
    { domain: "theglobeandmail.com", nameCN: "环球邮报", country: "Canada", flag: "🇨🇦", accessibleInChina: false },

    // 法国 (France)
    { domain: "rfi.fr", nameCN: "法国国际广播电台", country: "France", flag: "🇫🇷", accessibleInChina: false },
    { domain: "afp.com", nameCN: "法新社", country: "France", flag: "🇫🇷", accessibleInChina: false },
    { domain: "lemonde.fr", nameCN: "世界报", country: "France", flag: "🇫🇷", accessibleInChina: false },

    // 德国 (Germany)
    { domain: "dw.com", nameCN: "德国之声", country: "Germany", flag: "🇩🇪", accessibleInChina: false },
    { domain: "dpa.com", nameCN: "德国新闻社", country: "Germany", flag: "🇩🇪", accessibleInChina: false },
    { domain: "spiegel.de", nameCN: "明镜周刊", country: "Germany", flag: "🇩🇪", accessibleInChina: false },

    // 澳大利亚 (Australia)
    { domain: "abc.net.au", nameCN: "澳大利亚广播公司", country: "Australia", flag: "🇦🇺", accessibleInChina: false },
    { domain: "smh.com.au", nameCN: "悉尼先驱晨报", country: "Australia", flag: "🇦🇺", accessibleInChina: false },

    // 西班牙 (Spain)
    { domain: "elpais.com", nameCN: "国家报", country: "Spain", flag: "🇪🇸", accessibleInChina: false },

    // 意大利 (Italy)
    { domain: "ansa.it", nameCN: "安莎通讯社", country: "Italy", flag: "🇮🇹", accessibleInChina: false },

    // 国际 (International)
    { domain: "investing.com", nameCN: "英为财情", country: "International", flag: "🌍", accessibleInChina: true },
    { domain: "reuters.com", nameCN: "路透社", country: "International", flag: "🌍", accessibleInChina: false },
    { domain: "apnews.com", nameCN: "美联社", country: "International", flag: "🌍", accessibleInChina: false },

    // 卡塔尔 (Qatar)
    { domain: "aljazeera.com", nameCN: "半岛电视台", country: "Qatar", flag: "🇶🇦", accessibleInChina: true },

    // 阿联酋 (UAE)
    { domain: "alarabiya.net", nameCN: "阿拉伯卫星电视台", country: "UAE", flag: "🇦🇪", accessibleInChina: true },
    { domain: "gulfnews.com", nameCN: "海湾新闻", country: "UAE", flag: "🇦🇪", accessibleInChina: true },

    // 以色列 (Israel)
    { domain: "haaretz.com", nameCN: "国土报", country: "Israel", flag: "🇮🇱", accessibleInChina: true },
    { domain: "jpost.com", nameCN: "耶路撒冷邮报", country: "Israel", flag: "🇮🇱", accessibleInChina: true },

    // 土耳其 (Turkey)
    { domain: "aa.com.tr", nameCN: "阿纳多卢通讯社", country: "Turkey", flag: "🇹🇷", accessibleInChina: true },

    // 埃及 (Egypt)
    { domain: "ahram.org.eg", nameCN: "金字塔报", country: "Egypt", flag: "🇪🇬", accessibleInChina: true },

    // 俄罗斯 (Russia)
    { domain: "sputniknews.com", nameCN: "卫星通讯社", country: "Russia", flag: "🇷🇺", accessibleInChina: true },
    { domain: "rt.com", nameCN: "今日俄罗斯", country: "Russia", flag: "🇷🇺", accessibleInChina: true },
    { domain: "tass.com", nameCN: "塔斯社", country: "Russia", flag: "🇷🇺", accessibleInChina: true },
    { domain: "ria.ru", nameCN: "俄新社", country: "Russia", flag: "🇷🇺", accessibleInChina: true },
    { domain: "kommersant.ru", nameCN: "生意人报", country: "Russia", flag: "🇷🇺", accessibleInChina: true },

    // 日本 (Japan)
    { domain: "nhk.or.jp", nameCN: "日本广播协会", country: "Japan", flag: "🇯🇵", accessibleInChina: true },
    { domain: "kyodonews.net", nameCN: "共同社", country: "Japan", flag: "🇯🇵", accessibleInChina: true },
    { domain: "nikkei.com", nameCN: "日本经济新闻", country: "Japan", flag: "🇯🇵", accessibleInChina: true },
    { domain: "asahi.com", nameCN: "朝日新闻", country: "Japan", flag: "🇯🇵", accessibleInChina: true },

    // 新加坡 (Singapore)
    { domain: "zaobao.com.sg", nameCN: "联合早报", country: "Singapore", flag: "🇸🇬", accessibleInChina: true },
    { domain: "straitstimes.com", nameCN: "海峡时报", country: "Singapore", flag: "🇸🇬", accessibleInChina: true },

    // 韩国 (South Korea)
    { domain: "chosun.com", nameCN: "朝鲜日报", country: "South Korea", flag: "🇰🇷", accessibleInChina: true },
    { domain: "joongang.co.kr", nameCN: "中央日报", country: "South Korea", flag: "🇰🇷", accessibleInChina: true },
    { domain: "yna.co.kr", nameCN: "韩联社", country: "South Korea", flag: "🇰🇷", accessibleInChina: true },

    // 印度 (India)
    { domain: "ptinews.com", nameCN: "印度报业托拉斯", country: "India", flag: "🇮🇳", accessibleInChina: true },
    { domain: "timesofindia.indiatimes.com", nameCN: "印度时报", country: "India", flag: "🇮🇳", accessibleInChina: true },

    // 中国大陆 (China)
    { domain: "xinhuanet.com", nameCN: "新华社", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "people.com.cn", nameCN: "人民日报", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "jiemian.com", nameCN: "界面新闻", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "thepaper.cn", nameCN: "澎湃新闻", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "infzm.com", nameCN: "南方周末", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "gmw.cn", nameCN: "光明网", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "ce.cn", nameCN: "中国经济网", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "81.cn", nameCN: "中国军网", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "qstheory.cn", nameCN: "求是网", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "bjnews.com.cn", nameCN: "新京报", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "chinanews.com", nameCN: "中国新闻网", country: "China", flag: "🇨🇳", accessibleInChina: true },
    { domain: "cnr.cn", nameCN: "中国广播网", country: "China", flag: "🇨🇳", accessibleInChina: true },

    // 中国台湾 (Taiwan)
    { domain: "cna.com.tw", nameCN: "中央通讯社", country: "Taiwan", flag: "🇹🇼", accessibleInChina: true },

    // 巴西 (Brazil)
    { domain: "folha.uol.com.br", nameCN: "圣保罗页报", country: "Brazil", flag: "🇧🇷", accessibleInChina: true },
    { domain: "oglobo.globo.com", nameCN: "环球报", country: "Brazil", flag: "🇧🇷", accessibleInChina: true },

    // 阿根廷 (Argentina)
    { domain: "clarin.com", nameCN: "号角报", country: "Argentina", flag: "🇦🇷", accessibleInChina: true },
    { domain: "lanacion.com.ar", nameCN: "民族报", country: "Argentina", flag: "🇦🇷", accessibleInChina: true },

    // 智利 (Chile)
    { domain: "emol.com", nameCN: "信使报", country: "Chile", flag: "🇨🇱", accessibleInChina: true },

    // 哥伦比亚 (Colombia)
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

        // 1. 处理评分
        let max_rate_display = "";
        if (max_rate_class && max_rate_score !== null && max_rate_score !== undefined) {
            max_rate_display = `
            <div class="article-rating mt-2">
                ${max_rate_class}：
                ${createRatingStars(max_rate_score)}
            </div>`;
        }

        // 2. 处理 AI 服务和模型信息 (新增)
        const ai_service_url = appendix.__AI_SERVICE__ || '';
        const ai_model_name = escapeHTML(appendix.__AI_MODEL__ || '');
        let ai_info_display = "";

        if (ai_service_url || ai_model_name) {
            let service_host = "";
            try {
                if (ai_service_url) {
                    service_host = new URL(ai_service_url).hostname;
                }
            } catch (e) { /* Ignore invalid URL */ }

            ai_info_display = `
            <div class="ai-metadata mt-1 text-secondary small" style="font-size: 0.85em;">
                <i class="bi bi-robot" title="AI Model"></i>
                <strong>${ai_model_name || 'Unknown Model'}</strong>
                ${service_host ? `
                    <span class="mx-1 text-muted">|</span>
                    <i class="bi bi-clouds" title="Service Provider"></i> ${service_host}
                ` : ''}
            </div>`;
        }

        // 3. 处理归档时间
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
                ${ai_info_display}
                <div class="mt-1">
                    <span class="debug-label">UUID:</span> ${uuid}
                </div>
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
