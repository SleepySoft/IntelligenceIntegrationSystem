"""历史导入兼容壳；情报域资源实现位于 :mod:`ServiceComponent.DomainResources`。"""

from ServiceComponent.DomainResources import DomainContext, DomainResourceRegistry

SubsystemContext = DomainContext
SubsystemRegistry = DomainResourceRegistry

__all__ = [
    "DomainContext", "DomainResourceRegistry", "SubsystemContext", "SubsystemRegistry",
]
