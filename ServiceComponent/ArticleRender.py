import datetime

from Tools.DateTimeUtility import any_time_to_time_str
from ServiceComponent.IntelligenceHubDefines import APPENDIX_MANUAL_RATING


RATING_SCRIPT = """
<script>
// 验证评分输入
function validateRating(input) {
    if (input.value < 0) {
        input.value = 0;
    } else if (input.value > 10) {
        input.value = 10;
    }
}

$(document).ready(function() {
    // 提交评分处理
    $('#submit-rating').click(function() {
        const ratings = {};
        const uuid = '{uuid_val}';  // 从模板变量获取UUID

        // 收集所有评分
        $('input.form-control').each(function() {
            if (this.id.startsWith('rating-')) {
                const dimension = this.id.replace('rating-', '');
                const score = $(this).val();
                ratings[dimension] = score;
            }
        });

        // 发送评分数据到服务器
        $.ajax({
            url: '/manual_rate',  // 需要你实现这个API端点
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                uuid: uuid,
                ratings: ratings,
                timestamp: new Date().toISOString()
            }),
            success: function(response) {
                showNotification('Ratings submitted successfully!', 'success');
            },
            error: function(xhr, status, error) {
                showNotification('Error submitting ratings: ' + error, 'danger');
            }
        });
    });

    // 显示通知的函数
    function showNotification(message, type) {
        // 创建Bootstrap Toast通知
        const toastHtml = `
            <div class="toast align-items-center text-bg-${type} border-0" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="d-flex">
                    <div class="toast-body">
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                </div>
            </div>
        `;

        $('#toast-container').html(toastHtml);
        $('.toast').toast('show');
    }
});
</script>

<!-- 添加Toast容器 -->
<div id="toast-container" class="toast-container position-fixed bottom-0 end-0 p-3"></div>
"""


# Create rating stars display
def create_rating_stars(score):
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


def gen_rating_table(article_dict: dict) -> str:
    rates = article_dict.get('RATE', {})
    manual_ratings = article_dict.get('APPENDIX', { }).get(APPENDIX_MANUAL_RATING, { })

    rating_table = ""
    if rates:
        rating_table = '<div class="mt-4"><h5><i class="bi bi-graph-up"></i> Analysis & Evaluation</h5>'
        rating_table += '<div class="table-responsive"><table class="table table-sm">'
        rating_table += '<thead><tr><th>Dimension</th><th>Rating</th><th>Manual Rating</th></tr></thead><tbody>'

        for key, score in rates.items():
            if isinstance(score, (int, float)) and 0 <= score <= 10:
                # 获取手动评分值，如果没有则使用默认评分
                manual_score = manual_ratings.get(key, score)

                rating_table += f'''
                <tr>
                    <td>{key}</td>
                    <td>{create_rating_stars(score)}</td>
                    <td>
                        <input id="rating-{key}" type="number" class="form-control form-control-sm" 
                               value="{manual_score}" min="0" max="10" step="0.5"
                               style="width: 80px;" oninput="validateRating(this)">
                    </td>
                </tr>
                '''

        rating_table += '</tbody></table>'
        rating_table += f'''
            <div class="mt-3">
                <button id="submit-rating" class="btn btn-primary btn-sm float-end">
                    <i class="bi bi-check-circle"></i> Submit Manual Ratings
                </button>
            </div>
        </div></div>
        '''

    return rating_table


def default_article_render(article_dict: dict):
    """
    Renders article dictionary data into a formatted HTML page

    Parameters:
        article_dict (dict): Dictionary containing article data

    Returns:
        str: Formatted HTML string
    """
    # Safely get data that might be empty
    uuid_val = article_dict.get('UUID', '')
    informant = article_dict.get('INFORMANT', '')
    pub_time = article_dict.get('PUB_TIME', 'N/A')
    event_times = article_dict.get('TIME', [])
    locations = article_dict.get('LOCATION', [])
    people = article_dict.get('PEOPLE', [])
    organizations = article_dict.get('ORGANIZATION', [])
    title = article_dict.get('EVENT_TITLE', 'No Title')
    brief = article_dict.get('EVENT_BRIEF', 'No Brief')
    content = article_dict.get('EVENT_TEXT', 'No Content')
    rates = article_dict.get('RATE', {})
    impact = article_dict.get('IMPACT', 'No Impact')
    tips = article_dict.get('TIPS', 'No Tips')

    pub_time_display = any_time_to_time_str(pub_time)
    formatted_times = [any_time_to_time_str(item) for item in event_times]

    # Build rating table
    rating_table = gen_rating_table(article_dict)

    similar_btn_html = f'''
        <a href="/search_page_url?mode=similar&target_uuid={uuid_val}" target="_blank" class="btn btn-outline-light btn-sm ms-2">
            <i class="bi bi-intersect"></i> Find Similar
        </a>
        '''

    # Build HTML
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">

        <style>
            .article-header {{
                background: linear-gradient(to right, #1a2980, #26d0ce);
                color: white;
                padding: 2.5rem 0;
                margin-bottom: 2rem;
                border-radius: 0 0 10px 10px;
            }}
            .meta-box {{
                background-color: rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 15px;
            }}
            .key-points {{
                border-left: 4px solid #0d6efd;
                padding-left: 15px;
                margin: 20px 0;
            }}
            .impact-card {{
                background: linear-gradient(to right, #f8f9fa, #e9ecef);
                border-radius: 10px;
                padding: 20px;
                border-left: 4px solid #dc3545;
                margin: 30px 0;
            }}
            .tip-card {{
                background-color: #d1ecf1;
                border-radius: 10px;
                padding: 15px;
                margin: 20px 0;
                border-left: 4px solid #0dcaf0;
            }}
            .content-section {{
                line-height: 1.8;
                font-size: 1.1rem;
                color: #444;
            }}
            footer {{
                border-top: 1px solid #eee;
                padding: 15px 0;
                margin-top: 30px;
                font-size: 0.9rem;
                color: #6c757d;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Article header -->
            <div class="article-header text-center">
                <div class="container">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <div class="text-start">
                            <small class="d-block"><i class="bi bi-calendar-event"></i> Published: {pub_time_display}</small>
                            <small class="d-block"><i class="bi bi-upc-scan"></i> {uuid_val}</small>
                        </div>
                        <h1 class="display-5 fw-bold">{title}</h1>
                        <div>
                            {f'<a href="{informant}" class="btn btn-sm btn-light"><i class="bi bi-link-45deg"></i> Source</a>' if informant else ''}
                            {similar_btn_html}
                        </div>
                    </div>
                    <div class="lead">{brief}</div>
                </div>
            </div>

            <!-- Metadata section -->
            <div class="row">
                <div class="col-md-4">
                    <div class="meta-box">
                        <h5><i class="bi bi-geo-alt"></i> Geographic Locations</h5>
                        {', '.join(locations) if locations else "No location data"}
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="meta-box">
                        <h5><i class="bi bi-people"></i> Related People</h5>
                        {', '.join(people) if people else "No associated people"}
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="meta-box">
                        <h5><i class="bi bi-building"></i> Related Organizations</h5>
                        {', '.join(organizations) if organizations else "No related organizations"}
                    </div>
                </div>
            </div>

            <!-- Event time section -->
            <div class="row mt-2">
                <div class="col-12">
                    <div class="meta-box">
                        <h5><i class="bi bi-clock-history"></i> Event Time(s)</h5>
                        {', '.join(formatted_times) if formatted_times else "No specific timing data"}
                    </div>
                </div>
            </div>

            <!-- Main content -->
            <div class="content-section mt-4">
                {content}
            </div>

            <!-- Analysis section -->
            {rating_table}

            <!-- Impact analysis -->
            <div class="impact-card">
                <h5><i class="bi bi-lightning-charge"></i> Potential Impact</h5>
                <p>{impact}</p>
            </div>

            <!-- Tips section -->
            <div class="tip-card">
                <h5><i class="bi bi-lightbulb"></i> Analyst Notes</h5>
                <p>{tips}</p>
            </div>
        </div>

        <footer>
            <div class="container">
                <div class="d-flex justify-content-between">
                    <div>
                        <div class="system-name">
                            <i class="bi bi-cpu"></i> Intelligence Integration System
                        </div>
                        <div class="mt-2 text-muted">© {datetime.datetime.now().year} All rights reserved</div>
                    </div>
                    <div class="text-end">
                        <div class="author-brand">SleepySoft</div>
                        <div class="tagline">Turning data into insight</div>
                    </div>
                </div>
                <div class="text-center mt-4 pt-3 border-top border-secondary border-opacity-25">
                    <small class="d-block opacity-75">
                        Report generated at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </small>
                </div>
            </div>
        </footer>
        
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>

        {RATING_SCRIPT.replace('{uuid_val}', uuid_val)}
    </body>
    </html>
    """

    return html_content
