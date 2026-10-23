"""
IIS 系统仪表盘页面渲染器。

原 `/statistics/intelligence_statistics.html` 为按时间分布查询页面，
现已改造为自动刷新的 IIS 系统仪表盘，展示：
- 情报提交流量统计（最近 24 小时及按小时/天/周/月分布）
- IIS 内部状态（队列长度、计数器、AI 客户端健康、线程信息）
- 系统/进程资源占用
- 用于排查卡顿与 CPU 问题的诊断信息
"""

from flask import render_template


def get_intelligence_statistics_page() -> str:
    """Render the IIS system dashboard page."""
    return render_template('intelligence_dashboard.html')
