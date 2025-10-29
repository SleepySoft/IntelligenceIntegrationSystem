import datetime
from urllib.parse import urlencode

from ServiceComponent.ArticleTableRender import generate_articles_table, article_table_style


def render_query_page(params, results, total_results):
    total_pages = max(1, (total_results + params['per_page'] - 1) // params['per_page'])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Intelligence Query</title>
        <!-- 引入Bootstrap CSS -->
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            {article_table_style}
            
            .card {{ margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .form-label {{ font-weight: 500; }}
            .pagination {{ margin-top: 20px; }}
            .results-header {{ 
                background-color: #f8f9fa; 
                padding: 15px;
                border-radius: 5px;
                margin-bottom: 15px;
            }}
            .required:after {{ content: "*"; color: red; }}
        </style>
    </head>
    <body>
        <div class="container py-4">
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h2>Intelligence Query Form</h2>
                </div>
                <div class="card-body">
                    <form method="POST">
                        <div class="row g-3">
                            <!-- Time Range -->
                            <div class="col-md-6">
                                <label class="form-label required">Start Time (UTC)</label>
                                <input type="datetime-local" class="form-control" 
                                    name="start_time" value="{params['start_time']}">
                            </div>
                            <div class="col-md-6">
                                <label class="form-label required">End Time (UTC)</label>
                                <input type="datetime-local" class="form-control" 
                                    name="end_time" value="{params['end_time']}">
                            </div>

                            <!-- ID Fields -->
                            <div class="col-md-4">
                                <label class="form-label">Location IDs</label>
                                <input type="text" class="form-control" 
                                    name="locations" placeholder="Comma-separated IDs" 
                                    value="{params['locations']}">
                            </div>
                            <div class="col-md-4">
                                <label class="form-label">People IDs</label>
                                <input type="text" class="form-control" 
                                    name="peoples" placeholder="Comma-separated IDs" 
                                    value="{params['peoples']}">
                            </div>
                            <div class="col-md-4">
                                <label class="form-label">Organization IDs</label>
                                <input type="text" class="form-control" 
                                    name="organizations" placeholder="Comma-separated IDs" 
                                    value="{params['organizations']}">
                            </div>
                            <div class="col-md-2">
                                <label class="form-label">Results per page</label>
                                <select class="form-select" name="per_page">
                                    <option value="10" {"selected" if params['per_page'] == 10 else ""}>10</option>
                                    <option value="20" {"selected" if params['per_page'] == 20 else ""}>20</option>
                                    <option value="50" {"selected" if params['per_page'] == 50 else ""}>50</option>
                                </select>
                            </div>
                            <div class="col-md-2">
                                <label class="form-label">Page</label>
                                <input type="number" class="form-control" 
                                    name="page" min="1" value="{params['page']}">
                            </div>
                        </div>

                        <div class="mt-4">
                            <button type="submit" class="btn btn-primary">Search</button>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- Results Section -->
            <div class="results-header">
                <h3>Query Results</h3>
                <p class="mb-0">Showing {len(results)} of {total_results} records</p>
            </div>

            {generate_articles_table(results)}

            <!-- Pagination -->
            <nav aria-label="Page navigation">
                <ul class="pagination justify-content-center">
                    <li class="page-item {"disabled" if params['page'] == 1 else ""}">
                        <a class="page-link" href="?page={params['page'] - 1}&{urlencode(params)}">Previous</a>
                    </li>
                    {"".join(
        f'<li class="page-item {"active" if i == params["page"] else ""}">'
        f'<a class="page-link" href="?page={i}&{urlencode(params)}">{i}</a></li>'
        for i in range(1, min(total_pages + 1, 11))
    )}
                    <li class="page-item {"disabled" if params['page'] >= total_pages else ""}">
                        <a class="page-link" href="?page={params['page'] + 1}&{urlencode(params)}">Next</a>
                    </li>
                </ul>
            </nav>
            
        </div>

        <!-- 引入Bootstrap JS -->
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """



RECYCLED = """
            <!-- Results Section -->
            <div class="results-header">
                <h3>Query Results</h3>
                <p class="mb-0">Showing {len(results)} of {total_results} records</p>
            </div>
            
            {generate_articles_table(results)}
            
            <!-- Pagination -->
            <nav aria-label="Page navigation">
                <ul class="pagination justify-content-center">
                    <li class="page-item {"disabled" if params['page'] == 1 else ""}">
                        <a class="page-link" href="?page={params['page'] - 1}&{urlencode(params)}">Previous</a>
                    </li>
                    {"".join(
        f'<li class="page-item {"active" if i == params["page"] else ""}">'
        f'<a class="page-link" href="?page={i}&{urlencode(params)}">{i}</a></li>'
        for i in range(1, min(total_pages + 1, 11))
    )}
                    <li class="page-item {"disabled" if params['page'] >= total_pages else ""}">
                        <a class="page-link" href="?page={params['page'] + 1}&{urlencode(params)}">Next</a>
                    </li>
                </ul>
            </nav>
"""


