"""IIS Hub 的公共入口。

业务运行时已迁移到 ``ServiceComponent.HubApplication``；本模块只保留主工程
使用的数据模型导出，不持有数据库、AI、向量或后台扩展的直接依赖。
"""

from ServiceComponent.HubApplication import HubApplication
from ServiceComponent.IntelligenceHubDefines_v2 import *  # noqa: F401,F403


IntelligenceHub = HubApplication
