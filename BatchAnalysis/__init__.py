"""批量分析子系统：积压情报的 Provider Batch 通道（可整体禁用）。

- `service.py`：BatchAnalysisService（构建/提交/轮询/校验/回注，SQLite 状态机）
- `config.py`：optional_subsystems.batch_analysis 配置读取
- `web.py`：/api/control/batch_analysis 端点
- `iis_integration.py`：唯一装配入口（mount_to_iis）
"""

from .config import BatchAnalysisConfig
from .service import BatchAnalysisService

__all__ = ["BatchAnalysisConfig", "BatchAnalysisService"]
