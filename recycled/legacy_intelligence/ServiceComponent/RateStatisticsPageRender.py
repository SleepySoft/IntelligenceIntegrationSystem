from jinja2 import Template

BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Score Distribution Chart</title>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/luxon@3.0.4/build/global/luxon.min.js"></script>
    <style>
        {{ css_content }}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <h1><i class="fas fa-chart-bar"></i> Score Distribution Analysis</h1>
            <p class="subtitle">Visualize and analyze score data over time</p>
        </header>

        <div class="control-panel">
            <div class="time-controls">
                <div class="input-group">
                    <label for="startTime"><i class="fas fa-play-circle"></i> Start Time:</label>
                    <input type="datetime-local" id="startTime" class="time-input">
                </div>
                <div class="input-group">
                    <label for="endTime"><i class="fas fa-stop-circle"></i> End Time:</label>
                    <input type="datetime-local" id="endTime" class="time-input">
                </div>
            </div>
            <button class="generate-btn" onclick="fetchData()">
                <i class="fas fa-sync-alt"></i> Generate Report
            </button>
        </div>

        <div class="chart-container">
            <canvas id="distributionChart"></canvas>
        </div>

        <div class="stats-container" id="stats">
            <h3><i class="fas fa-chart-pie"></i> Statistics</h3>
            <div class="stats-content">
                <p>Please select a time range and generate report to view statistics</p>
            </div>
        </div>
    </div>
    
    <script>
        const QUERY_URL = "{{ query_url }}";
    </script>

    <script>
        {{ js_content }}
    </script>
</body>
</html>
"""

CSS_CONTENT = """
:root {
    --primary-color: #4361ee;
    --secondary-color: #3a0ca3;
    --accent-color: #7209b7;
    --light-color: #f8f9fa;
    --dark-color: #212529;
    --success-color: #4cc9f0;
    --warning-color: #f72585;
    --gray-color: #6c757d;
    --light-gray: #e9ecef;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Roboto', sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: var(--dark-color);
    min-height: 100vh;
    padding: 20px;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    background: white;
    border-radius: 15px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
    overflow: hidden;
}

.header {
    background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
    color: white;
    padding: 30px;
    text-align: center;
}

.header h1 {
    font-size: 2.5rem;
    font-weight: 700;
    margin-bottom: 10px;
}

.header .subtitle {
    font-size: 1.1rem;
    opacity: 0.9;
    font-weight: 300;
}

.control-panel {
    padding: 30px;
    background: var(--light-gray);
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
    align-items: end;
    justify-content: space-between;
}

.time-controls {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
}

.input-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.input-group label {
    font-weight: 500;
    color: var(--dark-color);
    font-size: 0.9rem;
}

.time-input {
    padding: 12px 16px;
    border: 2px solid #ddd;
    border-radius: 8px;
    font-size: 1rem;
    transition: all 0.3s ease;
    min-width: 250px;
}

.time-input:focus {
    outline: none;
    border-color: var(--primary-color);
    box-shadow: 0 0 0 3px rgba(67, 97, 238, 0.1);
}

.generate-btn {
    background: linear-gradient(135deg, var(--accent-color), var(--secondary-color));
    color: white;
    border: none;
    padding: 14px 28px;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    gap: 8px;
}

.generate-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.2);
}

.generate-btn:active {
    transform: translateY(0);
}

.chart-container {
    padding: 30px;
    height: 500px;
}

.stats-container {
    padding: 30px;
    background: var(--light-color);
    border-top: 1px solid var(--light-gray);
}

.stats-container h3 {
    color: var(--primary-color);
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
}

.stats-content {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 20px;
}

.stat-card {
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
    border-left: 4px solid var(--primary-color);
}

.stat-card h4 {
    color: var(--gray-color);
    font-size: 0.9rem;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
}

.stat-card p {
    font-size: 1.8rem;
    font-weight: 700;
    color: var(--primary-color);
}

@media (max-width: 768px) {
    .control-panel {
        flex-direction: column;
        align-items: stretch;
    }

    .time-controls {
        flex-direction: column;
    }

    .time-input {
        min-width: 100%;
    }

    .header h1 {
        font-size: 2rem;
    }

    .chart-container {
        height: 400px;
        padding: 15px;
    }
}
"""

JS_CONTENT = """
let chart = null;
let startPicker = null;
let endPicker = null;

function initializeDateTimePickers() {
    // Set default time range (last 30 days)
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 30);

    document.getElementById('startTime').value = formatDateTimeLocal(start);
    document.getElementById('endTime').value = formatDateTimeLocal(end);
}

function formatDateTimeLocal(date) {
    return date.toISOString().slice(0, 16);
}

function formatDisplayDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function fetchData() {
    const startTime = document.getElementById('startTime').value;
    const endTime = document.getElementById('endTime').value;

    if (!startTime || !endTime) {
        showAlert('Please select both start and end times', 'warning');
        return;
    }

    // Validate time range
    if (new Date(startTime) >= new Date(endTime)) {
        showAlert('Start time must be before end time', 'error');
        return;
    }

    showLoading();

    // Convert to ISO format
    const startISO = new Date(startTime).toISOString();
    const endISO = new Date(endTime).toISOString();

    fetch(`${QUERY_URL}?start_time=${startISO}&end_time=${endISO}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                renderChart(data.chart_data);
                renderStats(data);
                showAlert('Report generated successfully!', 'success');
            } else {
                throw new Error(data.error || 'Unknown error occurred');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showAlert('Failed to fetch data: ' + error.message, 'error');
        })
        .finally(() => {
            hideLoading();
        });
}

function renderChart(chartData) {
    const ctx = document.getElementById('distributionChart').getContext('2d');

    // Destroy previous chart if exists
    if (chart) {
        chart.destroy();
    }

    // Generate colors for bars
    const backgroundColors = chartData.map((_, index) => {
        const hue = (index * 30) % 360;
        return `hsla(${hue}, 70%, 65%, 0.7)`;
    });

    chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: chartData.map(item => `Score ${item.score}`),
            datasets: [{
                label: 'Number of Records',
                data: chartData.map(item => item.count),
                backgroundColor: backgroundColors,
                borderColor: backgroundColors.map(color => color.replace('0.7', '1')),
                borderWidth: 2,
                borderRadius: 6,
                hoverBackgroundColor: backgroundColors.map(color => color.replace('0.7', '0.9'))
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    titleFont: {
                        size: 14
                    },
                    bodyFont: {
                        size: 13
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.1)'
                    },
                    title: {
                        display: true,
                        text: 'Number of Records',
                        font: {
                            size: 14,
                            weight: 'bold'
                        }
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    title: {
                        display: true,
                        text: 'Score Value',
                        font: {
                            size: 14,
                            weight: 'bold'
                        }
                    }
                }
            },
            animation: {
                duration: 1000,
                easing: 'easeOutQuart'
            }
        }
    });
}

function formatDisplayDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function renderStats(data) {
    const statsDiv = document.getElementById('stats');

    statsDiv.innerHTML = `
        <h3><i class="fas fa-chart-pie"></i> Statistics</h3>
        <div class="stats-content">
            <div class="stat-card">
                <h4>Time Range Start</h4>
                <p>${formatDisplayDate(data.time_range.start)}</p>
            </div>
            <div class="stat-card">
                <h4>Time Range End</h4>
                <p>${formatDisplayDate(data.time_range.end)}</p>
            </div>
            <div class="stat-card">
                <h4>Total Records</h4>
                <p>${data.total_records.toLocaleString()}</p>
            </div>
            <div class="stat-card">
                <h4>Date Generated</h4>
                <p>${new Date().toLocaleDateString('zh-CN')}</p>
            </div>
        </div>
    `;
}

function showAlert(message, type = 'info') {
    // Remove existing alerts
    const existingAlerts = document.querySelectorAll('.alert');
    existingAlerts.forEach(alert => alert.remove());

    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.innerHTML = `
        <i class="fas fa-${getAlertIcon(type)}"></i>
        ${message}
    `;

    // Add styles for alert
    alert.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        border-radius: 8px;
        color: white;
        font-weight: 500;
        z-index: 1000;
        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
        background: ${getAlertColor(type)};
        display: flex;
        align-items: center;
        gap: 10px;
    `;

    document.body.appendChild(alert);

    // Auto remove after 5 seconds
    setTimeout(() => {
        if (alert.parentNode) {
            alert.parentNode.removeChild(alert);
        }
    }, 5000);
}

function getAlertIcon(type) {
    const icons = {
        success: 'check-circle',
        error: 'exclamation-circle',
        warning: 'exclamation-triangle',
        info: 'info-circle'
    };
    return icons[type] || 'info-circle';
}

function getAlertColor(type) {
    const colors = {
        success: 'linear-gradient(135deg, #4cc9f0, #4361ee)',
        error: 'linear-gradient(135deg, #f72585, #7209b7)',
        warning: 'linear-gradient(135deg, #ffaa00, #ff6b00)',
        info: 'linear-gradient(135deg, #00b4d8, #0077b6)'
    };
    return colors[type] || colors.info;
}

function showLoading() {
    const btn = document.querySelector('.generate-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';
    btn.disabled = true;

    // Store original text for later restoration
    btn.dataset.originalText = originalText;
}

function hideLoading() {
    const btn = document.querySelector('.generate-btn');
    if (btn.dataset.originalText) {
        btn.innerHTML = btn.dataset.originalText;
    }
    btn.disabled = false;
}

// Initialize on load
window.addEventListener('load', function() {
    initializeDateTimePickers();

    // Add event listeners for input validation
    document.getElementById('startTime').addEventListener('change', validateTimeRange);
    document.getElementById('endTime').addEventListener('change', validateTimeRange);
});

function validateTimeRange() {
    const startTime = document.getElementById('startTime').value;
    const endTime = document.getElementById('endTime').value;

    if (startTime && endTime && new Date(startTime) >= new Date(endTime)) {
        document.getElementById('startTime').style.borderColor = '#f72585';
        document.getElementById('endTime').style.borderColor = '#f72585';
    } else {
        document.getElementById('startTime').style.borderColor = '';
        document.getElementById('endTime').style.borderColor = '';
    }
}
"""


def get_statistics_page(query_url: str) -> str:
    template = Template(BASE_TEMPLATE)
    return template.render({
        'query_url': query_url,
        'css_content': CSS_CONTENT,
        'js_content': JS_CONTENT
    })
