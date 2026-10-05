"""将 IIS 现有基础设施接入运行时端口的适配器。"""

from ServiceComponent.adapters.iis_pipeline import IISPipelinePorts
from ServiceComponent.adapters.iis_vector import IISVectorExtension
from ServiceComponent.adapters.iis_translation import IISAsyncTranslationExtension

__all__ = ["IISAsyncTranslationExtension", "IISPipelinePorts", "IISVectorExtension"]
