import re
import html
from typing import List

from ServiceComponent.IntelligenceHubDefines import APPENDIX_MAX_RATE_CLASS, APPENDIX_MAX_RATE_SCORE
from ServiceComponent.IntelligenceHubDefines_v2 import APPENDIX_TIME_GOT, APPENDIX_TIME_ARCHIVED


article_table_style = """
.article-list { 
    max-width: 1000px; 
    margin: 0 auto; 
    background: white;
    padding: 25px;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
}
.article-list h1 {
    color: #343a40;
    border-bottom: 2px solid #e9ecef;
    padding-bottom: 15px;
    margin-bottom: 25px;
}
.article-card {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 25px;
    transition: all 0.3s ease;
    background: white;
}
.article-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 7px 15px rgba(0,0,0,0.1);
    border-color: #c5cae9;
}
.article-title {
    color: #1a73e8;
    text-decoration: none;
    font-weight: 600;
    font-size: 1.4rem;
    display: block;
    margin-bottom: 8px;
}
.article-title:hover { 
    text-decoration: underline; 
    color: #0d47a1;
}
.article-meta {
    color: #5f6368;
    font-size: 0.95em;
    margin: 10px 0;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
}
.article-time {
    background: #e3f2fd;
    padding: 3px 8px;
    border-radius: 4px;
}
.article-source {
    color: #4a4a4a;
    background: #e3f2fd;
    padding: 3px 8px;
    border-radius: 4px;
}
.source-link {
    color: #1565c0;
    text-decoration: none;
}
.source-link:hover {
    text-decoration: underline;
}
.article-summary {
    color: #202124;
    line-height: 1.7;
    margin: 15px 0;
    font-size: 1.05rem;
}
.debug-info {
    background-color: #f5f5f5;
    border-left: 3px solid #90a4ae;
    padding: 10px 10px;
    margin-top: 5px;
    font-size: 0.85rem;
    color: #546e7a;
    border-radius: 0 4px 4px 0;
    word-break: break-all;
}
.debug-label {
    font-weight: 600;
    color: #37474f;
    margin-right: 5px;
}
.debug-link {
    color: #0288d1;
    text-decoration: none;
}
.debug-link:hover {
    text-decoration: underline;
}
.article-source {
    /* 使用flex布局，确保 "Source:"、图标、URL能良好对齐 */
    display: flex;
    align-items: baseline; /* 基线对齐，视觉效果更佳 */
    flex-wrap: nowrap; /* 不允许 "Source:" 和图标换行 */
}

.source-link-container {
    /* 这个容器将包裹图标和链接，以便它们能作为一个整体换行 */
    display: inline-flex;
    align-items: baseline;
    flex-wrap: wrap; /* 允许URL过长时换行 */
}

.source-prefix {
    /* 图标的前缀样式 */
    display: inline-block; /* 确保图标能和文字同行 */
    margin-right: 6px; /* 和URL之间增加一点间距 */
    font-size: 1.1em;  /* 让图标稍微大一点，更清晰 */
    vertical-align: middle; /* 垂直居中对齐 */
}

.domain-highlight {
    /* 域名高亮样式 */
    background-color: #FFFF00; /* 亮黄色，类似荧光笔 */
    padding: 1px 2px;
    border-radius: 3px;
}

/* 确保链接本身在容器内可以正常表现 */
.source-link {
    word-break: break-all; /* 允许长URL在任意位置断开换行 */
}
"""


article_table_color_gradient_script = """
<script>
function updateTimeBackgrounds() {
    const now = new Date().getTime();
    const twelveHours = 12 * 60 * 60 * 1000;  // 12小时毫秒数

    document.querySelectorAll('.archived-time').forEach(el => {
        const archivedTime = new Date(el.dataset.archived).getTime();
        const timeDiff = now - archivedTime;

        // 计算颜色比例（0-12小时）
        let ratio = Math.min(1, Math.max(0, timeDiff / twelveHours));

        // 起始色：橙色 (#FFA500)，终止色：浅蓝色 (#E3F2FD)
        const r = Math.round(255 - ratio * (255 - 227));
        const g = Math.round(165 - ratio * (165 - 242));
        const b = Math.round(0 - ratio * (0 - 253));

        el.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
    });
}

document.addEventListener('DOMContentLoaded', updateTimeBackgrounds);
setInterval(updateTimeBackgrounds, 60000);
</script>
"""


article_source_enhancer_script = """
<script>

document.addEventListener('DOMContentLoaded', () => {
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

    /**
     * 根据主机名在媒体库中查找匹配项
     * @param {string} hostname - 链接的主机名 (e.g., "www.wsj.com")
     * @returns {object|null} - 匹配到的媒体对象或null
     */
    function findSourceInfo(hostname) {
        // 先完全匹配
        let source = mediaSources.find(s => s.domain === hostname);
        if (source) return source;
        // 再匹配子域名
        source = mediaSources.find(s => hostname.endsWith('.' + s.domain));
        return source || null;
    }

    /**
     * 提取顶级域名部分用于高亮
     * @param {string} hostname - 链接的主机名
     * @returns {string|null} - 顶级域名 (e.g., "wsj.com", "bbc.co.uk")
     */
    function getHighlightDomain(hostname) {
        // 匹配常见的二级域名后缀，如 .co.uk, .com.cn
        const complexTldMatch = hostname.match(/[^.]+\.(?:co|com|net|org|gov|edu)\.[^.]+$/);
        if (complexTldMatch) {
            return complexTldMatch[0];
        }
        // 匹配标准的顶级域名
        const simpleTldMatch = hostname.match(/[^.]+\.[^.]+$/);
        return simpleTldMatch ? simpleTldMatch[0] : hostname;
    }

    // 遍历页面上所有的 .article-source 元素
    document.querySelectorAll('.article-source').forEach(sourceElement => {
        const link = sourceElement.querySelector('a.source-link');
        if (!link || !link.href) return;

        try {
            const url = new URL(link.href);
            const hostname = url.hostname;
            const sourceInfo = findSourceInfo(hostname);

            // 创建一个容器来包裹图标和链接，以便统一处理换行
            const container = document.createElement('div');
            container.className = 'source-link-container';

            // 1. 创建图标前缀
            const prefixSpan = document.createElement('span');
            prefixSpan.className = 'source-prefix';

            if (sourceInfo) {
                const accessibilityIcon = sourceInfo.accessibleInChina ? '✅' : '🚫';
                prefixSpan.textContent = ` ${accessibilityIcon} ${sourceInfo.flag}`;
            } else {
                prefixSpan.textContent = ' ❔  🌍'; // 默认地球图标
            }

            // 2. 高亮域名
            const highlightPart = getHighlightDomain(hostname);
            const originalText = link.textContent;

            if (originalText.includes(highlightPart)) {
                const highlightedHTML = originalText.replace(
                    highlightPart,
                    `<span class="domain-highlight">${highlightPart}</span>`
                );
                link.innerHTML = highlightedHTML;
            }

            // 3. 更新DOM结构
            // 将图标和链接移入新容器
            container.appendChild(prefixSpan);
            container.appendChild(link);

            // 将原来的 "Source: " 文本节点和新容器一起放回
            const sourceTextNode = sourceElement.firstChild;
            sourceElement.innerHTML = ''; // 清空原有内容
            sourceElement.appendChild(sourceTextNode);
            sourceElement.appendChild(container);

        } catch (e) {
            console.error('Error processing source link:', e);
        }
    });
});
</script>
"""


def generate_articles_table(articles: List[dict]):
    """
    Generate HTML for articles list that can be reused across pages

    Parameters:
    articles -- List of article dictionaries [{
        "UUID": str,
        "INFORMANT": str,  # Source information (could be URL or text)
        "TIME": str (YYYY-MM-DD),
        "EVENT_TITLE": str,
        "EVENT_BRIEF": str
    }]
    """

    # Escape all text content to prevent XSS attacks
    def escape_text(text):
        return html.escape(str(text)) if text else ""

    # Check if valid URL
    def is_valid_url(url):
        return re.match(r'^https?://', url) if url else False

    # Generate rating stars display
    def create_rating_stars(score):
        """Convert numeric score to star rating display"""
        if not isinstance(score, (int, float)) or score < 0 or score > 10:
            return ""

        stars = ""
        full_stars = int(score) // 2
        half_star = (int(score) % 2 == 1)
        empty_stars = 5 - full_stars - (1 if half_star else 0)

        stars += ''.join(['<i class="bi bi-star-fill text-warning"></i> ' for _ in range(full_stars)])
        if half_star:
            stars += '<i class="bi bi-star-half text-warning"></i> '
        stars += ''.join(['<i class="bi bi-star text-warning"></i> ' for _ in range(empty_stars)])
        stars += f' <span class="ms-2 text-muted">{score}/10</span>'
        return stars

    # Build articles HTML
    articles_html = ""
    for article in articles:
        uuid = escape_text(article["UUID"])
        informant = escape_text(article.get("INFORMANT", ""))

        # Generate intelligence detail URL (for debugging)
        intel_url = f"/intelligence/{uuid}"

        # Build source information (clickable URL or plain text)
        informant_html = (
            f'<a href="{escape_text(informant)}" target="_blank" class="source-link">{informant}</a>'
            if is_valid_url(informant)
            else informant or 'Unknown Source'
        )

        # Safely get archived time from nested structure
        appendix = article.get('APPENDIX', {})
        archived_time = escape_text(appendix.get(APPENDIX_TIME_ARCHIVED, ''))

        # Safely get max rating information
        max_rate_class = escape_text(appendix.get(APPENDIX_MAX_RATE_CLASS, ''))
        max_rate_score = appendix.get(APPENDIX_MAX_RATE_SCORE)
        max_rate_display = ""

        # Generate rating display if valid data exists
        if max_rate_class and max_rate_score is not None:
            max_rate_display = f"""
            <div class="article-rating mt-2">
                {max_rate_class}：
                {create_rating_stars(max_rate_score)}
            </div>
            """

        archived_html = ""
        if archived_time:
            archived_html = f"""
            <span class="article-time archived-time" data-archived="{archived_time}">
                Archived: {archived_time}
            </span>
            """

        articles_html += f"""
        <div class="article-card">
            <h3>
                <a href="{intel_url}" target="_blank" class="article-title">
                    {escape_text(article.get("EVENT_TITLE", "No Title"))}
                </a>
            </h3>
            <div class="article-meta">
                {archived_html}
                <span class="article-time">Publish: {escape_text(article.get("PUB_TIME") or 'No Datetime')}</span>
                <span class="article-source">Source: {informant_html}</span>
            </div>
            <p class="article-summary">{escape_text(article.get("EVENT_BRIEF", "No Brief"))}</p>

            <!-- Debug information section -->
            <div class="debug-info">
                {max_rate_display}
                <span class="debug-label">UUID:</span> {uuid}
            </div>
        </div>
        """

    return articles_html

