"""引擎对存储的最小接口契约，按 Python Protocol 进行结构化类型约束。

实现类无需继承这些 Protocol，只要提供对应方法即可；类型标注不自动创建数据库、
检查方法、执行权限控制或事务。当前未加 runtime_checkable，不作为 isinstance
运行时检查接口使用。实现可为内存、数据库或真正支持持久化查询的文件 Repository。

协议不承诺线程安全、跨仓库事务、批量写入、删除、锁或并发版本比较；生产存储若需
这些能力，应在适配器和调用流程中明确实现。演示 JSON 注入工具不是本协议的实现。
"""
from __future__ import annotations
from typing import Protocol
from uuid import UUID
from .models import CanonicalEvent, EventRecord
from .queries import EventPage, EventQuery

class EventRepository(Protocol):
    """原始观察的登记与查询端口，供 core.EventEngine 调用。

    建议以 EventRecord.uuid 唯一存储并保留原观察，不把重复主键写成静默更新。
    接口没有 update：状态/身份变化写入 CanonicalEvent，而不是覆盖原观察。
    Registry 校验与角色映射由 Engine.register_event 完成，直接调用仓库会绕过它们。
    """

    def add(self, event: EventRecord) -> None:
        """保存一条观察，用于登记后的持久化或内存保存，不自动匹配 CanonicalEvent。

        event 已由接入/引擎校验、解析全局实体；本方法不查领域配置、不补 UUID 或时间。
        返回 None；主键重复和写失败应向调用方报告，具体异常类型由实现约定：
        当前内存抛 ValueError，MongoDB 可能抛驱动异常，协议不把它们统一包装。
        """
        ...

    def get(self, event_uuid: UUID) -> EventRecord | None:
        """按观察全局 UUID 取一条原记录，供分析、匹配和来源回溯。

        不接受局部 E1 或聚合 UUID，不做模糊查询；不存在返回 None。存储读取失败
        应抛实现约定的异常，不应伪装成“没有此观察”；是否返回深拷贝不由协议保证。
        """
        ...

    def search(self, query: EventQuery) -> EventPage:
        """执行观察级条件与分页，供通用查询和聚合成员重算使用。

        query 的空条件、角色/实体联动及限定词限制见 EventQuery；返回 items 和分页前
        total，排序由适配器约定。只返回已存观察，不进行身份绑定、状态投影或图推理。
        必须注意 UUID 列表查询不能因默认分页漏掉聚合重算所需的成员；协议本身不验证它。
        """
        ...

class CanonicalEventRepository(Protocol):
    """稳定事件身份及可重算物化结果的存储端口，与原观察仓库分工。

    用于 Engine.resolve_canonical 的候选召回、创建和更新。成员观察仍保存在
    EventRepository；只存本对象而丢失原观察，会失去重算和证据追溯能力。
    当前提供内存实现；不能据此接口声明推断 MongoDB 也已有聚合仓库实现。
    """

    def add(self, event: CanonicalEvent) -> None:
        """保存新稳定身份，通常在无候选/所有候选不兼容后由引擎创建。

        event 应由匹配器从首条观察生成，uuid 与成员观察 UUID 区分；此方法不自行
        校验成员或判定事实真值。重复身份应报告错误，不能覆盖已有成员；异常依实现。
        """
        ...

    def update(self, event: CanonicalEvent) -> None:
        """保存同 UUID 的新物化结果，供成功加入成员并完整重算后的更新使用。

        不应在此用“最后报道覆盖”代替状态算法，也不靠身份角色并集消除冲突。
        当前内存实现对不存在 UUID 抛 KeyError；协议未定义新增式 upsert、历史审计
        或 version 的 compare-and-swap，多写入者需额外并发控制，版本字段本身不提供锁。
        """
        ...

    def get(self, event_uuid: UUID) -> CanonicalEvent | None:
        """按聚合身份 UUID 读取物化结果，供候选确定后的更新或重复解析返回。

        不按成员观察 UUID 隐式解析，未找到返回 None；引擎期望 find_candidates 返回
        的候选可以随后 get 到，删除/并发一致性需由实际适配器保证。
        """
        ...

    def find_candidates(self, observation: EventRecord) -> tuple[CanonicalEvent, ...]:
        """为一条新观察召回可能同身份的聚合，随后由 CanonicalEventMatcher 精确判定。

        observation 不是查询已绑定事实的凭证，返回候选不意味着允许合并。召回可按
        谓词和角色等索引缩小范围，但应保守、完整，避免强过滤漏掉信息不完整的候选；
        漏召回会使引擎误以为应新建身份，重复观察检测也依赖候选能被找回。
        当前内存实现按同谓词且至少一个身份角色重叠、或候选无身份角色召回，不代表
        通用最优策略；无候选返回空元组，协议不保证顺序、分页、完备性或最大规模。
        """
        ...
