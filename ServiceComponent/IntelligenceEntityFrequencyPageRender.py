BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>实体出现频率统计</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --primary-color: #4361ee;
            --secondary-color: #3a0ca3;
            --accent-color: #7209b7;
            --success-color: #06d6a0;
            --warning-color: #ffd166;
            --danger-color: #ef476f;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f5f7fb;
            color: #333;
            padding-top: 20px;
        }
        .dashboard-header {
            background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
            color: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .card {
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            border: none;
            margin-bottom: 20px;
        }
        .card-header {
            background: linear-gradient(135deg, var(--primary-color), var(--accent-color));
            color: white;
            border-top-left-radius: 12px !important;
            border-top-right-radius: 12px !important;
            font-weight: 600;
        }
        .stats-card {
            text-align: center;
            padding: 20px;
        }
        .stats-value {
            font-size: 2.2rem;
            font-weight: 700;
            color: var(--primary-color);
            margin: 10px 0;
        }
        .stats-label {
            font-size: 0.95rem;
            color: #6c757d;
            font-weight: 500;
        }
        .control-panel {
            background-color: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            margin-bottom: 25px;
        }
        .chart-container {
            height: 420px;
            min-height: 420px;
            background-color: white;
            border-radius: 12px;
            padding: 15px;
        }
        .table-container {
            max-height: 400px;
            overflow-y: auto;
        }
        .nav-tabs .nav-link {
            border-radius: 8px;
            margin-right: 5px;
            font-weight: 500;
            color: #6c757d;
            padding: 10px 24px;
        }
        .nav-tabs .nav-link.active {
            background-color: var(--primary-color);
            color: white;
            border-color: var(--primary-color);
        }
        .entity-table th {
            position: sticky;
            top: 0;
            background: #f8f9fa;
            z-index: 10;
        }
        .btn-primary {
            background-color: var(--primary-color);
            border-color: var(--primary-color);
            border-radius: 8px;
            padding: 8px 24px;
            font-weight: 500;
        }
        .btn-primary:hover {
            background-color: var(--secondary-color);
            border-color: var(--secondary-color);
        }
        .form-control, .form-select {
            border-radius: 8px;
        }
        .badge-top {
            background-color: var(--success-color);
            color: #fff;
        }
        .badge-bottom {
            background-color: var(--warning-color);
            color: #333;
        }
        .loading-overlay {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(255,255,255,0.85);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
            border-radius: 12px;
        }
        .spinner {
            width: 40px; height: 40px;
            border: 4px solid #f3f3f3;
            border-top: 4px solid var(--primary-color);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin { 0%{transform:rotate(0deg);} 100%{transform:rotate(360deg);} }
        footer { text-align:center; padding:20px; color:#6c757d; font-size:0.9rem; margin-top:20px; }
    </style>
</head>
<body>
<div class="container">
    <div class="dashboard-header">
        <div class="row align-items-center">
            <div class="col-md-8">
                <h1><i class="fas fa-project-diagram me-3"></i>实体出现频率统计</h1>
                <p class="mb-0">按小时粒度统计情报中地点、地域、人物、组织的出现频次与趋势</p>
            </div>
            <div class="col-md-4 text-end">
                <i class="fas fa-database fa-3x opacity-75"></i>
            </div>
        </div>
    </div>

    <!-- 控制面板 -->
    <div class="control-panel">
        <div class="row align-items-end">
            <div class="col-md-3">
                <label for="startTime" class="form-label fw-bold">开始时间</label>
                <input type="datetime-local" id="startTime" class="form-control">
            </div>
            <div class="col-md-3">
                <label for="endTime" class="form-label fw-bold">结束时间</label>
                <input type="datetime-local" id="endTime" class="form-control">
            </div>
            <div class="col-md-2">
                <label for="thresholdInput" class="form-label fw-bold">阈值（底部过滤）</label>
                <input type="number" id="thresholdInput" class="form-control" value="0" min="0">
            </div>
            <div class="col-md-2">
                <label for="topNInput" class="form-label fw-bold">TOP N</label>
                <input type="number" id="topNInput" class="form-control" value="20" min="1" max="100">
            </div>
            <div class="col-md-2 text-end">
                <button id="fetchData" class="btn btn-primary w-100">
                    <i class="fas fa-sync-alt me-2"></i>刷新
                </button>
            </div>
        </div>
    </div>

    <!-- Tab 导航 -->
    <ul class="nav nav-tabs mb-3" id="entityTab" role="tablist">
        <li class="nav-item" role="presentation">
            <button class="nav-link active" id="tab-location" data-bs-toggle="tab" data-bs-target="#pane-location"
                    type="button" role="tab" onclick="switchTab('LOCATION')">
                <i class="fas fa-map-marker-alt me-2"></i>地点
            </button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-geography" data-bs-toggle="tab" data-bs-target="#pane-geography"
                    type="button" role="tab" onclick="switchTab('GEOGRAPHY')">
                <i class="fas fa-globe me-2"></i>地域
            </button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-people" data-bs-toggle="tab" data-bs-target="#pane-people"
                    type="button" role="tab" onclick="switchTab('PEOPLE')">
                <i class="fas fa-user me-2"></i>人物
            </button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-organization" data-bs-toggle="tab" data-bs-target="#pane-organization"
                    type="button" role="tab" onclick="switchTab('ORGANIZATION')">
                <i class="fas fa-building me-2"></i>组织
            </button>
        </li>
    </ul>

    <!-- Tab 内容 -->
    <div class="tab-content" id="entityTabContent">
        <!-- 动态生成的 pane 结构一致，用 JS 填充 -->
        <div class="tab-pane fade show active" id="pane-location" role="tabpanel">
            <div id="content-LOCATION"></div>
        </div>
        <div class="tab-pane fade" id="pane-geography" role="tabpanel">
            <div id="content-GEOGRAPHY"></div>
        </div>
        <div class="tab-pane fade" id="pane-people" role="tabpanel">
            <div id="content-PEOPLE"></div>
        </div>
        <div class="tab-pane fade" id="pane-organization" role="tabpanel">
            <div id="content-ORGANIZATION"></div>
        </div>
    </div>

    <footer>
        <p>IntelligenceIntegrationSystem — 实体频率统计模块</p>
    </footer>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const ENTITY_TYPES = ['LOCATION', 'GEOGRAPHY', 'PEOPLE', 'ORGANIZATION'];
const ENTITY_LABELS = {
    'LOCATION': '地点',
    'GEOGRAPHY': '地域',
    'PEOPLE': '人物',
    'ORGANIZATION': '组织'
};
let currentEntityType = 'LOCATION';
let charts = {};

function formatDateTimeLocal(date) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function initPage() {
    const now = new Date();
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    document.getElementById('endTime').value = formatDateTimeLocal(now);
    document.getElementById('startTime').value = formatDateTimeLocal(yesterday);

    document.getElementById('fetchData').addEventListener('click', fetchAllData);

    // 初始化每个 tab 的占位结构
    ENTITY_TYPES.forEach(type => initTabStructure(type));

    fetchAllData();
}

function initTabStructure(entityType) {
    const container = document.getElementById(`content-${entityType}`);
    container.innerHTML = `
        <div class="loading-overlay" id="loading-${entityType}">
            <div class="spinner"></div>
        </div>
        <div class="row mb-3">
            <div class="col-md-3">
                <div class="card stats-card">
                    <div class="stats-value" id="stat-total-${entityType}">-</div>
                    <div class="stats-label">总提及次数</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stats-card">
                    <div class="stats-value" id="stat-unique-${entityType}">-</div>
                    <div class="stats-label">唯一实体数</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stats-card">
                    <div class="stats-value" id="stat-peak-${entityType}">-</div>
                    <div class="stats-label">最活跃时段</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stats-card">
                    <div class="stats-value" id="stat-peakcount-${entityType}">-</div>
                    <div class="stats-label">峰值次数</div>
                </div>
            </div>
        </div>
        <div class="row mb-3">
            <div class="col-12">
                <div class="card">
                    <div class="card-header"><i class="fas fa-chart-bar me-2"></i>趋势曲线 — ${ENTITY_LABELS[entityType]}</div>
                    <div class="card-body position-relative">
                        <div id="trend-chart-${entityType}" class="chart-container"></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="row">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header"><i class="fas fa-trophy me-2"></i>TOP 20 — ${ENTITY_LABELS[entityType]}</div>
                    <div class="card-body">
                        <div id="top-bar-${entityType}" style="height:320px;"></div>
                        <div class="table-container mt-3">
                            <table class="table table-sm table-hover entity-table" id="top-table-${entityType}">
                                <thead><tr><th>#</th><th>实体名称</th><th>总次数</th></tr></thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header"><i class="fas fa-arrow-down me-2"></i>高于阈值的 BOTTOM 20 — ${ENTITY_LABELS[entityType]}</div>
                    <div class="card-body">
                        <div class="table-container">
                            <table class="table table-sm table-hover entity-table" id="bottom-table-${entityType}">
                                <thead><tr><th>#</th><th>实体名称</th><th>总次数</th></tr></thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function switchTab(entityType) {
    currentEntityType = entityType;
    setTimeout(() => {
        if (charts[`trend-${entityType}`]) charts[`trend-${entityType}`].resize();
        if (charts[`top-${entityType}`]) charts[`top-${entityType}`].resize();
    }, 150);
}

async function fetchAllData() {
    const startTime = document.getElementById('startTime').value;
    const endTime = document.getElementById('endTime').value;
    const threshold = document.getElementById('thresholdInput').value;
    const topN = document.getElementById('topNInput').value;

    if (!startTime || !endTime) {
        alert('请选择开始和结束时间');
        return;
    }

    ENTITY_TYPES.forEach(type => {
        document.getElementById(`loading-${type}`).style.display = 'flex';
    });

    for (const entityType of ENTITY_TYPES) {
        try {
            const url = `/statistics/entity_frequency?entity_type=${entityType}&start_time=${encodeURIComponent(startTime)}&end_time=${encodeURIComponent(endTime)}&top_n=${topN}&bottom_threshold=${threshold}`;
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            renderEntityData(entityType, data);
        } catch (err) {
            console.error(`Failed to load ${entityType}:`, err);
            document.getElementById(`content-${entityType}`).innerHTML += `<div class="alert alert-danger mt-3">加载失败: ${err.message}</div>`;
        } finally {
            document.getElementById(`loading-${entityType}`).style.display = 'none';
        }
    }
}

function renderEntityData(entityType, data) {
    // 概览卡片
    const summary = data.summary || {};
    document.getElementById(`stat-total-${entityType}`).textContent = summary.total_mentions ?? 0;
    document.getElementById(`stat-unique-${entityType}`).textContent = summary.unique_entities ?? 0;
    document.getElementById(`stat-peak-${entityType}`).textContent = (summary.peak_hour || '').replace('T', ' ');
    document.getElementById(`stat-peakcount-${entityType}`).textContent = summary.peak_hour_count ?? 0;

    const timeSlots = data.time_slots || [];
    const topEntities = data.top_entities || [];
    const bottomEntities = data.bottom_entities || [];

    // 趋势图
    renderTrendChart(entityType, timeSlots, topEntities);

    // TOP 柱状图
    renderTopBarChart(entityType, topEntities);

    // TOP 表格
    const topTbody = document.querySelector(`#top-table-${entityType} tbody`);
    topTbody.innerHTML = topEntities.map((e, i) =>
        `<tr><td><span class="badge badge-top">${i+1}</span></td><td>${escapeHtml(e.name)}</td><td>${e.total_count}</td></tr>`
    ).join('');

    // BOTTOM 表格
    const bottomTbody = document.querySelector(`#bottom-table-${entityType} tbody`);
    bottomTbody.innerHTML = bottomEntities.map((e, i) =>
        `<tr><td><span class="badge badge-bottom">${i+1}</span></td><td>${escapeHtml(e.name)}</td><td>${e.total_count}</td></tr>`
    ).join('');
}

function renderTrendChart(entityType, timeSlots, topEntities) {
    const domId = `trend-chart-${entityType}`;
    let chart = charts[`trend-${entityType}`];
    if (!chart) {
        chart = echarts.init(document.getElementById(domId));
        charts[`trend-${entityType}`] = chart;
    }

    const labels = timeSlots.map(s => s.replace('T', '\\n'));
    const series = topEntities.slice(0, 10).map((e, idx) => ({
        name: e.name,
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        data: e.trend || [],
        emphasis: { focus: 'series' },
    }));

    const option = {
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255,255,255,0.95)',
            textStyle: { color: '#333' }
        },
        legend: {
            type: 'scroll',
            top: 0,
            data: topEntities.slice(0, 10).map(e => e.name)
        },
        grid: { left: '3%', right: '4%', bottom: '10%', top: '15%', containLabel: true },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: labels,
            axisLabel: { rotate: 30, fontSize: 11 }
        },
        yAxis: {
            type: 'value',
            name: '出现次数',
            splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
        },
        series: series,
        color: [
            '#4361ee','#3a0ca3','#7209b7','#f72585','#4cc9f0',
            '#06d6a0','#ffd166','#ef476f','#118ab2','#073b4c'
        ]
    };
    chart.setOption(option, true);
}

function renderTopBarChart(entityType, topEntities) {
    const domId = `top-bar-${entityType}`;
    let chart = charts[`top-${entityType}`];
    if (!chart) {
        chart = echarts.init(document.getElementById(domId));
        charts[`top-${entityType}`] = chart;
    }

    const slice = topEntities.slice(0, 20);
    const names = slice.map(e => e.name);
    const counts = slice.map(e => e.total_count);

    const option = {
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            backgroundColor: 'rgba(255,255,255,0.95)',
            textStyle: { color: '#333' }
        },
        grid: { left: '3%', right: '4%', bottom: '3%', top: '3%', containLabel: true },
        xAxis: {
            type: 'value',
            splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
        },
        yAxis: {
            type: 'category',
            data: names.reverse(),
            axisLabel: { fontSize: 11 }
        },
        series: [{
            type: 'bar',
            data: counts.reverse(),
            itemStyle: {
                borderRadius: [0, 4, 4, 0],
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                    { offset: 0, color: '#4361ee' },
                    { offset: 1, color: '#4cc9f0' }
                ])
            }
        }]
    };
    chart.setOption(option, true);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

window.addEventListener('resize', () => {
    Object.values(charts).forEach(c => c.resize());
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPage);
} else {
    initPage();
}
</script>
</body>
</html>
"""


def get_entity_frequency_page() -> str:
    """返回实体频率统计页面的完整 HTML 字符串。"""
    return BASE_TEMPLATE
