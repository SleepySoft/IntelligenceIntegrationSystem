/**
 * static/js/intelligence_table_renderer.js
 * 负责渲染逻辑
 */

class ArticleRenderer {
    // 构造函数支持传入两个分页容器ID（顶部和底部，如果只需要一个就传一个）
    constructor(listContainerId, paginationContainerClass = 'pagination-container') {
        this.listContainer = document.getElementById(listContainerId);
        this.paginationClass = paginationContainerClass;
        this.initAutoRefresh();
    }

    // --- 公共方法 ---

    render(articles, paginationInfo = null) {
        this.renderArticles(articles);

        if (paginationInfo) {
            this.renderPagination(
                paginationInfo.total,
                paginationInfo.page,
                paginationInfo.per_page
            );
        }

        this.enhanceSourceLinks();
        this.updateTimeBackgrounds();
    }

    showLoading() {
        if (this.listContainer) {
            // 移除 Bootstrap spinner，改用纯文字或自定义样式
            this.listContainer.innerHTML = `
                <div class="loading-spinner">
                    Loading Intelligences...
                </div>`;
        }
    }

    showError(message) {
        if (this.listContainer) {
            this.listContainer.innerHTML = `
                <div style="color: red; padding: 20px; text-align: center;">
                    Error: ${message}
                </div>`;
        }
    }

    formatLocalTime(timeStr) {
        if (!timeStr) return 'No Datetime';

        // 尝试解析时间
        // 技巧：如果后端传的是 "2023-10-10 10:00:00" 这种不带时区的格式且你确定它是GMT，
        // 你可能需要在字符串后加 'Z' 或 ' GMT'，但在标准 ISO8601 格式下直接 parse 即可。
        let date = new Date(timeStr);

        // 如果解析失败（例如 Invalid Date），直接返回原字符串
        if (isNaN(date.getTime())) {
            // 尝试处理常见的 Python 默认字符串格式 (如果 new Date 失败的话)
            // 这里做一个兼容：如果原来不含时区信息，为了保险起见，可以视为 UTC
            // date = new Date(timeStr + 'Z');
            return timeStr;
        }

        // 格式化为：YYYY-MM-DD HH:mm
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0'); // 月份从0开始
        const d = String(date.getDate()).padStart(2, '0');
        const h = String(date.getHours()).padStart(2, '0');
        const min = String(date.getMinutes()).padStart(2, '0');

        return `${y}-${m}-${d} ${h}:${min}`;
    }

    // --- 文章列表渲染 ---
    renderArticles(articles) {
        if (!this.listContainer) return;

        if (!articles || articles.length === 0) {
            this.listContainer.innerHTML = '<p style="text-align:center; padding: 50px;">NO Intelligence</p>';
            return;
        }

        const html = articles.map(article => {
            // ============================================================
            // 1. 统一变量定义 (防止 ReferenceError)
            // ============================================================

            // 1.1 获取 Appendix (防止 undefined)
            const appendix = article.APPENDIX || {};

            // 1.2 ID 获取 (兼容 v1:UUID, v2:uuid, MongoDB:_id)
            const uuid = this.escapeHTML(article.UUID || article.uuid || article._id || "Unknown-UUID");
            const intelUrl = `/intelligence/${uuid}`;

            // 1.3 来源获取 (兼容 v2:INFORMANT, v1:informant, source)
            const informant_val = article.INFORMANT || article.informant || article.source || "";
            const informant = this.escapeHTML(informant_val);
            const informant_html = this.isValidUrl(informant)
                ? `<a href="${informant}" target="_blank" class="source-link">${informant}</a>`
                : (informant || 'Unknown Source');

            // 1.4 发布时间获取 (兼容 v2:APPENDIX, v1:PUB_TIME, 采集时间兜底)
            // 顺序: Appendix -> Root PUB_TIME -> Root pub_time -> Root collect_time
            const pub_time_raw = appendix['__TIME_PUB__'] || article.PUB_TIME || article.pub_time || article.collect_time;
            const pub_time_display = this.formatLocalTime(pub_time_raw);

            // 1.5 归档时间获取 (用于背景变色，必须在顶部定义)
            const raw_archived_time = appendix['__TIME_ARCHIVED__'] || '';
            const archived_time_display = this.formatLocalTime(raw_archived_time);

            // 生成归档时间 HTML 片段
            let archived_html = "";
            if (raw_archived_time) {
                // 注意：这里用到了 raw_archived_time，所以它必须在上面定义
                archived_html = `<span class="article-time archived-time" data-archived="${this.escapeHTML(raw_archived_time)}">Archived: ${archived_time_display}</span>`;
            }

            // 1.6 向量评分 (Vector Score)
            const vector_score = appendix['__VECTOR_SCORE__'];
            let vector_score_html = "";
            if (vector_score !== undefined && vector_score !== null) {
                const formattedScore = parseFloat(vector_score).toFixed(3);
                let badgeClass = vector_score >= 0.8 ? 'bg-success' :
                                 (vector_score >= 0.6 ? 'bg-primary' :
                                 (vector_score >= 0.4 ? 'bg-warning' : 'bg-danger'));
                vector_score_html = `<span class="badge ${badgeClass} similarity-badge"><span class="similarity-score">${formattedScore}</span></span>`;
            }

            // 1.7 AI 服务信息
            const ai_service = this.escapeHTML(appendix['__AI_SERVICE__'] || '');
            const ai_model = this.escapeHTML(appendix['__AI_MODEL__'] || '');

            // ============================================================
            // 2. 版本逻辑分支 (V1 vs V2) - 生成 left_content
            // ============================================================

            const prompt_version = appendix['__PROMPT_VERSION__'];
            // 判断是否为 v2 (存在版本号 且 >= 20)
            const is_v2 = prompt_version && !isNaN(Number(prompt_version)) && Number(prompt_version) >= 20;

            let left_content = "";

            if (is_v2) {
                // --- [V2 Logic] ---
                const taxonomy = this.escapeHTML(article.TAXONOMY || "Unclassified");
                const sub_categories = article.SUB_CATEGORY || [];
                const rate_dict = article.RATE || {};

                // 子分类标签
                let tags_html = "";
                if (Array.isArray(sub_categories) && sub_categories.length > 0) {
                    tags_html = `<div class="v2-tags-container">` +
                        sub_categories.map(tag => `<span class="v2-category-tag">${this.escapeHTML(tag)}</span>`).join('') +
                        `</div>`;
                }

                // 多维度评分
                const rating_html = this.createV2RatingList(rate_dict);

                left_content = `
                <div style="margin-bottom: 6px;">
                    <span class="debug-label" style="font-size:0.95rem; color:#1a73e8;">${taxonomy}</span>
                    <span class="version-badge" title="Prompt Version: ${prompt_version}">v${prompt_version}</span>
                </div>
                ${tags_html}
                ${rating_html}
                <div style="margin-top:4px;">
                    <span class="debug-label">UUID:</span> ${uuid}
                </div>`;

            } else {
                // --- [V1 Logic] ---
                const max_rate_class = this.escapeHTML(appendix['__MAX_RATE_CLASS__'] || '');
                const max_rate_score = appendix['__MAX_RATE_SCORE__'];

                if (max_rate_class && max_rate_score !== null) {
                    left_content += `
                    <div class="article-rating">
                        <span class="debug-label">${max_rate_class}:</span>
                        ${this.createRatingStars(max_rate_score)}
                    </div>`;
                }

                left_content += `
                <div>
                    <span class="debug-label">UUID:</span> ${uuid}
                </div>`;
            }

            // ============================================================
            // 3. 构建右侧调试信息 (right_content)
            // ============================================================
            let right_content = "";
            if (ai_service || ai_model) {
                if (ai_service) right_content += `<div><span class="debug-label">Service:</span><span class="debug-value-truncate" title="${ai_service}">${ai_service}</span></div>`;
                if (ai_model) right_content += `<div><span class="debug-label">Model:</span><span class="debug-value-truncate" title="${ai_model}">${ai_model}</span></div>`;
            }

            // ============================================================
            // 4. 返回最终 HTML
            // ============================================================
            return `
            <div class="article-card">
                <h3>
                    <a href="${intelUrl}" target="_blank" class="article-title">
                        ${this.escapeHTML(article.EVENT_TITLE || article.title || "No Title")}
                    </a>
                </h3>
                <div class="article-meta">
                    ${archived_html}
                    <span class="article-time">Publish: ${pub_time_display}</span>
                    ${vector_score_html}
                    <span class="article-source">Source: ${informant_html}</span>
                </div>
                <p class="article-summary">${this.escapeHTML(article.EVENT_BRIEF || "No Brief")}</p>

                <div class="debug-info">
                    <div class="debug-left">
                        ${left_content}
                    </div>
                    <div class="debug-right">
                        ${right_content}
                    </div>
                </div>
            </div>`;
        }).join('');

        this.listContainer.innerHTML = html;
    }

    // --- 新增: V2 评分列表生成辅助函数 ---
    createV2RatingList(rateDict) {
        if (!rateDict || Object.keys(rateDict).length === 0) return "";

        // 将对象转换为数组并排序（可选：按分数降序或按Key排序，这里默认按Key）
        const entries = Object.entries(rateDict);

        let html = '<div class="v2-rating-list">';

        entries.forEach(([key, score]) => {
            const numScore = Number(score);
            // 复用 createRatingStars 但需要微调样式，这里直接手写简化版以适配紧凑布局
            let stars = "";
            const full_stars = Math.floor(numScore / 2);
            const half_star = (numScore % 2 >= 1);
            const empty_stars = 5 - full_stars - (half_star ? 1 : 0);

            for(let i=0; i<full_stars; i++) stars += '<i class="bi bi-star-fill text-warning"></i>';
            if(half_star) stars += '<i class="bi bi-star-half text-warning"></i>';
            for(let i=0; i<empty_stars; i++) stars += '<i class="bi bi-star text-warning" style="color:#dee2e6 !important"></i>'; // 空星颜色淡一点

            html += `
            <div class="v2-rating-row">
                <span class="v2-rating-label" title="${key}">${key}</span>
                <span style="display:inline-flex; align-items:center;">${stars}</span>
                <span class="v2-rating-score-text">${numScore}</span>
            </div>`;
        });

        html += '</div>';
        return html;
    }

    // --- 分页渲染：恢复原始 HTML 结构 ---
    renderPagination(total_results, current_page, per_page) {
        const containers = document.querySelectorAll('.' + this.paginationClass);
        if (!containers.length) return;

        // 计算逻辑
        const total_pages = Math.max(1, Math.ceil(total_results / per_page));
        current_page = Number(current_page);

        const has_prev = current_page > 1;
        const has_next = current_page < total_pages;

        // 生成原始风格的 HTML
        // <div class="pagination">
        //     <a class="page-btn head">1</a> (原始代码里有 return to 1)
        //     <a class="page-btn prev">Prev</a>
        //     <span class="page-info"> page / total </span>
        //     <a class="page-btn next">Next</a>
        // </div>

        let html = '<div class="pagination">';

        // 首页按钮 (可选，根据你的习惯)
        if (has_prev) {
            html += `<a class="page-btn" data-page="1">First</a>`;
            html += `<a class="page-btn" data-page="${current_page - 1}">Prev</a>`;
        } else {
            // 保持布局稳定的占位符或禁用状态
             html += `<span class="page-btn disabled">First</span>`;
             html += `<span class="page-btn disabled">Prev</span>`;
        }

        // 中间信息
        html += `<span class="page-info">${current_page} / ${total_pages} (Total: ${total_results})</span>`;

        // 下一页按钮
        if (has_next) {
            html += `<a class="page-btn" data-page="${current_page + 1}">Next</a>`;
        } else {
            html += `<span class="page-btn disabled">Next</span>`;
        }

        html += '</div>';

        // 填充到所有分页容器中
        containers.forEach(el => el.innerHTML = html);
    }

    // --- 样式增强逻辑 (保持不变) ---
    createRatingStars(score) {
        const numScore = Number(score);
        if (isNaN(numScore) || numScore < 0 || numScore > 10) return "";
        let stars = "";
        let full_stars = Math.floor(numScore / 2);
        let half_star = (numScore % 2 >= 1);
        let empty_stars = 5 - full_stars - (half_star ? 1 : 0);

        // 注意：这里依赖 Bootstrap Icons (bi-star...)
        for(let i=0; i<full_stars; i++) stars += '<i class="bi bi-star-fill text-warning"></i> ';
        if(half_star) stars += '<i class="bi bi-star-half text-warning"></i> ';
        for(let i=0; i<empty_stars; i++) stars += '<i class="bi bi-star text-warning"></i> ';

        stars += ` <span style="margin-left:8px; color:#6c757d;">${numScore.toFixed(1)}/10</span>`;
        return stars;
    }

    updateTimeBackgrounds() {
        const now = new Date().getTime();
        const twelveHours = 12 * 60 * 60 * 1000;
        const container = this.listContainer || document;
        container.querySelectorAll('.archived-time').forEach(el => {
            const archivedStr = el.dataset.archived;
            if(!archivedStr) return;
            const archivedTime = new Date(archivedStr.replace(/-/g, '/')).getTime();
            if (isNaN(archivedTime)) return;
            const timeDiff = now - archivedTime;
            let ratio = Math.min(1, Math.max(0, timeDiff / twelveHours));
            const r = Math.round(255 - ratio * (255 - 227));
            const g = Math.round(165 - ratio * (165 - 242));
            const b = Math.round(0 - ratio * (0 - 253));
            el.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            // 原始代码没有变色逻辑，如果你想要完全还原，可以删掉下面这行
            el.style.color = ratio < 0.3 ? '#fff' : '#5f6368';
        });
    }

    enhanceSourceLinks() {
        const container = this.listContainer || document;
        const findSourceInfo = (hostname) => {
            let source = ArticleRenderer.mediaSources.find(s => s.domain === hostname);
            if (source) return source;
            source = ArticleRenderer.mediaSources.find(s => hostname.endsWith('.' + s.domain));
            return source || null;
        };
        const getHighlightDomain = (hostname) => {
            const complexTldMatch = hostname.match(/[^.]+\.(?:co|com|net|org|gov|edu)\.[^.]+$/);
            if (complexTldMatch) return complexTldMatch[0];
            const simpleTldMatch = hostname.match(/[^.]+\.[^.]+$/);
            return simpleTldMatch ? simpleTldMatch[0] : hostname;
        };

        container.querySelectorAll('.article-source').forEach(sourceElement => {
            if(sourceElement.querySelector('.source-link-container')) return;
            const link = sourceElement.querySelector('a.source-link');
            if (!link || !link.href) return;
            try {
                const url = new URL(link.href);
                const hostname = url.hostname;
                const sourceInfo = findSourceInfo(hostname);
                const div = document.createElement('div');
                div.className = 'source-link-container';
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
                if (link.parentNode === sourceElement) {
                    div.appendChild(prefixSpan);
                    div.appendChild(link);
                    const sourceTextNode = sourceElement.firstChild;
                    sourceElement.innerHTML = '';
                    sourceElement.appendChild(sourceTextNode);
                    sourceElement.appendChild(div);
                }
            } catch (e) {
                console.error('Error processing source link:', e);
            }
        });
    }

    initAutoRefresh() {
        setInterval(() => this.updateTimeBackgrounds(), 60000);
    }

    escapeHTML(str) {
        if (str === null || str === undefined) return "";
        return String(str).replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    isValidUrl(url) {
        if (!url) return false;
        return url.match(/^(https?|ftp):\/\//) !== null;
    }
}

// 媒体来源数据库 (作为类的静态属性挂载)
ArticleRenderer.mediaSources = [
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
