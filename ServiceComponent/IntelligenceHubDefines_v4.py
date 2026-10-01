from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


FrameTuple = tuple[str, str, str]


def _predicate(
        label: str,
        definition: str,
        frame: FrameTuple,
        required_roles: tuple[str, ...],
        optional_roles: tuple[str, ...] = (),
        identity_roles: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "label": label,
        "definition": definition,
        "frame": frame,
        "required_roles": required_roles,
        "optional_roles": optional_roles,
        "identity_roles": identity_roles or required_roles,
    }


# Small, closed event families. Specific methods remain in predicate.surface.
PREDICATE_SPECS: dict[str, dict[str, Any]] = {
    # State and relation
    "exist": _predicate("存在", "主体存在或处于某种整体状态，仅作状态兜底", ("state", "intrinsic", "non_agentive"), ("subject",)),
    "possess": _predicate("持有", "主体持有实体、资产或权利", ("state", "relational", "agentive"), ("holder", "asset")),
    "control": _predicate("控制", "主体支配实体、组织、区域或资源", ("state", "relational", "agentive"), ("controller", "controlled")),
    "member_of": _predicate("隶属", "主体属于或隶属于组织体系", ("state", "relational", "non_agentive"), ("member", "organization")),
    "located_at": _predicate("位于", "实体位于或部署于某位置", ("state", "relational", "non_agentive"), ("subject", "location")),
    "depend_on": _predicate("依赖", "主体持续依赖对象或条件", ("state", "relational", "non_agentive"), ("dependent", "dependency")),
    "connected_to": _predicate("连接", "两个或多个参与者存在通信、运输或结构连接", ("state", "relational", "non_agentive"), ("participant",)),
    "capable": _predicate("具备能力", "主体具备某项能力或资格", ("state", "intrinsic", "non_agentive"), ("subject", "capability")),
    "valid": _predicate("有效", "政策、协议、许可或资格当前有效", ("state", "intrinsic", "non_agentive"), ("subject",)),
    "shortage": _predicate("短缺", "资源、产品或能力供给不足", ("state", "relational", "non_agentive"), ("resource",), ("affected",)),

    # State change
    "increase": _predicate("增加", "数量、规模、价格或程度增加", ("change", "intrinsic", "unknown"), ("subject",), ("agent",)),
    "decrease": _predicate("减少", "数量、规模、价格或程度减少", ("change", "intrinsic", "unknown"), ("subject",), ("agent",)),
    "improve": _predicate("改善", "质量、能力或表现向有利方向变化", ("change", "intrinsic", "unknown"), ("subject",), ("agent",)),
    "deteriorate": _predicate("恶化", "非物理对象的质量、能力或表现向不利方向变化", ("change", "intrinsic", "unknown"), ("subject",), ("agent",)),
    "create": _predicate("建立", "实体、组织、制度或关系从无到有", ("change", "intrinsic", "agentive"), ("actor", "object"), identity_roles=("object",)),
    "terminate": _predicate("终止", "实体、关系、资格或活动永久结束", ("change", "intrinsic", "unknown"), ("subject",), ("agent",)),
    "gain": _predicate("取得", "主体以非交易方式取得能力、资格、领土或控制", ("change", "relational", "unknown"), ("subject", "object"), ("agent",)),
    "lose": _predicate("失去", "主体失去能力、资格、领土或控制", ("change", "relational", "unknown"), ("subject", "object"), ("agent",)),
    "damage": _predicate("损毁", "人员、设施或物理对象受到损伤或损毁", ("change", "intrinsic", "unknown"), ("affected",), ("actor",)),
    "casualty": _predicate("伤亡", "人员死亡、受伤或失踪", ("change", "intrinsic", "unknown"), ("affected",), ("actor",)),
    "restore": _predicate("恢复", "对象恢复功能、状态或关系", ("change", "intrinsic", "unknown"), ("affected",), ("actor",)),
    "appoint": _predicate("任命", "人员取得职务或制度角色", ("change", "relational", "agentive"), ("authority", "person", "position"), identity_roles=("person", "position")),
    "remove": _predicate("离任", "人员辞职、被解除职务或失去制度角色", ("change", "relational", "unknown"), ("person", "position"), ("authority",)),
    "discover": _predicate("发现", "原本未知的对象或事实被识别", ("change", "targeted", "agentive"), ("discoverer", "object"), identity_roles=("object",)),
    "default": _predicate("违约", "债务人未按约履行偿付义务", ("change", "relational", "unknown"), ("debtor", "obligation")),
    "insolvency": _predicate("破产", "企业或主体进入法定或事实上的资不抵债状态", ("change", "intrinsic", "unknown"), ("subject",)),
    "violate": _predicate("违反", "主体违反规则、法律、协议或义务", ("change", "relational", "agentive"), ("actor", "rule")),

    # Transfer and exchange
    "move": _predicate("移动", "人员、装备或实体改变空间位置，包括撤退、增援和疏散", ("change", "transfer", "unknown"), ("theme",), ("source", "destination", "agent"), ("theme", "source", "destination")),
    "transfer": _predicate("转移", "权利、责任、订单或控制关系改变归属", ("change", "transfer", "agentive"), ("theme",), ("source", "destination", "agent"), ("theme", "source", "destination")),
    "trade": _predicate("交易", "普通商品或服务通过买卖在双方间交换，不含企业控制权收购", ("change", "transfer", "agentive"), ("goods",), ("buyer", "seller"), ("goods", "buyer", "seller")),
    "acquire": _predicate("收购", "通过交易取得企业、业务、股权、资产组合或控制权", ("change", "transfer", "agentive"), ("acquirer", "asset"), ("seller",)),
    "invest": _predicate("投资", "为取得权益或回报而投入资金或资源", ("process", "transfer", "agentive"), ("investor", "recipient"), ("resource",)),
    "fund": _predicate("资助", "不以取得控制权为核心，向主体或活动提供资金", ("process", "transfer", "agentive"), ("provider", "recipient"), ("resource",)),
    "aid": _predicate("援助", "政府、组织或个人无偿或优惠提供人道、军事或发展援助", ("process", "transfer", "agentive"), ("provider", "recipient"), ("goods",)),
    "pay": _predicate("支付", "付款方向收款方支付资金", ("change", "transfer", "agentive"), ("payer", "payee")),
    "lend": _predicate("放贷", "贷款方向借款人提供需偿还资金", ("change", "transfer", "agentive"), ("lender", "borrower")),
    "repay": _predicate("偿还", "债务人向债权人偿还债务", ("change", "transfer", "agentive"), ("debtor", "creditor")),
    "supply": _predicate("供应", "以供给关系向接收方持续或批量提供产品、物资或能力", ("process", "transfer", "agentive"), ("supplier", "recipient", "goods")),
    "communicate": _predicate("传播", "信息被正式发布、披露、分享或传递", ("process", "transfer", "agentive"), ("sender", "information"), ("recipient",)),

    # Operation and governance
    "operate": _predicate("运行", "设施、系统或组织开展常规活动", ("process", "intrinsic", "unknown"), ("subject",), ("operator",)),
    "produce": _predicate("生产", "制造或形成产品、材料或产出", ("process", "targeted", "agentive"), ("producer", "product")),
    "construct": _predicate("建设", "建造或扩建实体设施", ("process", "targeted", "agentive"), ("builder", "object"), identity_roles=("object",)),
    "develop": _predicate("研发", "研究、设计或开发技术、产品与能力", ("process", "targeted", "agentive"), ("developer", "object")),
    "test": _predicate("测试", "通过规定方法检验对象", ("process", "targeted", "agentive"), ("tester", "object")),
    "deploy": _predicate("部署", "将设备、软件或能力配置到目标环境", ("change", "targeted", "agentive"), ("deployer", "object"), ("location",), ("object", "location")),
    "maintain": _predicate("维护", "维护、维修或保障对象正常状态", ("process", "targeted", "agentive"), ("actor", "object")),
    "inspect": _predicate("检查", "检查、审计或验证对象状态与合规性", ("process", "targeted", "agentive"), ("inspector", "object")),
    "investigate": _predicate("调查", "围绕事件、人员或问题收集并核验信息", ("process", "targeted", "agentive"), ("investigator", "object")),
    "negotiate": _predicate("谈判", "多方围绕议题进行协商", ("process", "relational", "agentive"), ("party",), ("topic",)),
    "agree": _predicate("达成协议", "多方形成协议、合同或共同决定", ("change", "relational", "agentive"), ("party", "agreement")),
    "regulate": _predicate("监管", "主管主体制定、调整或执行规则、政策与标准", ("process", "targeted", "agentive"), ("authority", "target"), ("rule",)),
    "sanction": _predicate("制裁", "政治或监管主体对目标施加经济、金融、外交或人员制裁", ("change", "targeted", "agentive"), ("authority", "target"), ("object",)),
    "restrict": _predicate("限制", "主体对目标施加非制裁类准入、流动或行为限制", ("change", "targeted", "agentive"), ("authority", "target"), ("object",)),
    "elect": _predicate("选举", "通过选举使人员取得职位", ("change", "relational", "agentive"), ("electorate", "person", "position"), identity_roles=("person", "position")),
    "adjudicate": _predicate("裁决", "有权主体对案件或争议作出裁决", ("change", "targeted", "agentive"), ("authority", "case"), ("party",), ("case",)),
    "detain": _predicate("拘捕", "有权或实际控制方拘捕、拘留或俘获人员", ("change", "targeted", "agentive"), ("authority", "person"), identity_roles=("person",)),
    "seize": _predicate("扣押", "主体扣押、没收或冻结物品、资金和资产", ("change", "targeted", "agentive"), ("authority", "object"), identity_roles=("object",)),
    "protest": _predicate("抗议", "群体公开表达反对、诉求或抵制", ("process", "targeted", "agentive"), ("participant",), ("target",)),
    "cooperate": _predicate("合作", "多方持续开展合作或联合行动", ("process", "relational", "agentive"), ("party",), ("topic",)),
    "release": _predicate("发布", "产品、软件、文件或成果被正式发布", ("change", "transfer", "agentive"), ("releaser", "object"), ("recipient",)),

    # Conflict, security and hazard
    "armed_conflict": _predicate("武装冲突", "多方在一定时期和战区内持续武装对抗，不表示单次攻击", ("process", "relational", "agentive"), ("belligerent", "theater")),
    "attack": _predicate("攻击", "行动方对目标实施一次或一组紧密连续的敌对行动", ("process", "targeted", "agentive"), ("actor", "target"), ("instrument",)),
    "defend": _predicate("防御", "行动方保护目标并抵抗攻击", ("process", "targeted", "agentive"), ("actor", "target"), ("instrument",)),
    "intercept": _predicate("拦截", "对正在移动或来袭的对象实施阻断、接触或摧毁", ("process", "targeted", "agentive"), ("actor", "target"), ("instrument",)),
    "observe": _predicate("侦察", "搜索、监视、巡逻或侦察目标", ("process", "targeted", "agentive"), ("actor", "target"), ("instrument",)),
    "disrupt": _predicate("干扰", "主动干扰系统、行动、供应或通信正常运行", ("process", "targeted", "agentive"), ("actor", "target"), ("instrument",)),
    "accident": _predicate("事故", "非故意事故造成异常、损失或中断", ("change", "intrinsic", "non_agentive"), ("affected",)),
    "natural_hazard": _predicate("自然灾害", "地震、风暴、洪水等自然灾害发生", ("process", "intrinsic", "non_agentive"), ("phenomenon",), ("affected_area",)),
    "outbreak": _predicate("暴发", "疾病或有害现象在群体或地域中暴发传播", ("process", "intrinsic", "non_agentive"), ("phenomenon", "affected_area")),
}

EVENT_PREDICATE_IDS = tuple(PREDICATE_SPECS)

EventEntityType = Literal[
    "person", "organization", "geopolitical_entity", "location", "facility",
    "equipment", "product", "resource", "asset", "information", "policy",
    "agreement", "position", "capability", "topic", "phenomenon", "case",
    "event", "other_object",
]

EventDynamics = Literal["state", "process", "change"]
EventTopology = Literal["intrinsic", "relational", "targeted", "transfer"]
EventAgency = Literal["agentive", "non_agentive", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventEntityV4(StrictModel):
    id: str = Field(..., pattern=r"^ENT[1-9][0-9]*$")
    name: str = Field(..., min_length=1)
    type: EventEntityType
    country_code: str | None = Field(None, pattern=r"^[A-Z]{2}$")


class EventFrameV4(StrictModel):
    dynamics: EventDynamics
    topology: EventTopology
    agency: EventAgency


class EventPredicateV4(StrictModel):
    id: str | None = Field(..., json_schema_extra={"enum": [*EVENT_PREDICATE_IDS, None]})
    surface: str = Field(..., min_length=1)
    gloss: str | None = Field(None, min_length=1)

    @field_validator("id")
    @classmethod
    def check_predicate_id(cls, value):
        if value is not None and value not in PREDICATE_SPECS:
            raise ValueError(f"unsupported predicate id: {value}")
        return value

    @model_validator(mode="after")
    def check_gloss(self):
        if self.id is None and not self.gloss:
            raise ValueError("gloss is required when predicate id is null")
        if self.id is not None and self.gloss is not None:
            raise ValueError("gloss is only allowed when predicate id is null")
        return self


class EventTimeExpressionV4(StrictModel):
    normalized: str | None
    precision: Literal["year", "month", "day", "hour", "minute"]
    approximate: bool
    surface: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_normalized_precision(self):
        if self.normalized is None:
            return self
        patterns = {
            "year": r"^\d{4}$",
            "month": r"^\d{4}-\d{2}$",
            "day": r"^\d{4}-\d{2}-\d{2}$",
            "hour": r"^\d{4}-\d{2}-\d{2}T\d{2}$",
            "minute": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
        }
        import re
        if not re.fullmatch(patterns[self.precision], self.normalized):
            raise ValueError("normalized time does not match precision")
        return self


class EventTimeV4(StrictModel):
    event_time: EventTimeExpressionV4 | None = None
    start_time: EventTimeExpressionV4 | None = None
    end_time: EventTimeExpressionV4 | None = None
    effective_time: EventTimeExpressionV4 | None = None
    deadline: EventTimeExpressionV4 | None = None
    expected_start_time: EventTimeExpressionV4 | None = None
    expected_end_time: EventTimeExpressionV4 | None = None

    @model_validator(mode="after")
    def check_time_fields(self):
        values = self.model_dump(exclude_none=True)
        if not values:
            raise ValueError("time must contain at least one field")
        if self.event_time is not None and (self.start_time is not None or self.end_time is not None):
            raise ValueError("event_time cannot coexist with start_time or end_time")
        return self


class NumberAttributeV4(StrictModel):
    type: Literal["number"]
    value: float
    unit: str = Field(..., min_length=1)
    surface: str | None = None


class MoneyAttributeV4(StrictModel):
    type: Literal["money"]
    value: float
    currency: str = Field(..., pattern=r"^[A-Z]{3}$")
    unit: str | None = None
    surface: str | None = None


class RatioAttributeV4(StrictModel):
    type: Literal["ratio"]
    value: float
    surface: str | None = None

    @field_validator("value")
    @classmethod
    def check_ratio(cls, value):
        if not 0 <= value <= 1:
            raise ValueError("ratio value must be between 0 and 1")
        return value


class DurationAttributeV4(StrictModel):
    type: Literal["duration"]
    value: float
    unit: str = Field(..., min_length=1)
    surface: str | None = None


class EventAttributesV4(StrictModel):
    amount: MoneyAttributeV4 | None = None
    quantity: NumberAttributeV4 | None = None
    ratio: RatioAttributeV4 | None = None
    value_before: NumberAttributeV4 | None = None
    value_after: NumberAttributeV4 | None = None
    delta: NumberAttributeV4 | None = None
    duration: DurationAttributeV4 | None = None
    level: NumberAttributeV4 | None = None

    @model_validator(mode="after")
    def require_attribute(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("attributes must contain at least one field")
        return self


class EventContextV4(StrictModel):
    event_location: list[str] = Field(..., min_length=1)


QUALIFIER_VALUES_V4 = {
    "phase": {"not_started", "ongoing", "suspended", "completed", "cancelled", "blocked", "failed"},
    "intention": {"considering", "planned", "committed"},
    "authorization": {"required", "pending", "approved", "rejected", "revoked"},
    "directive": {"requested", "ordered", "required", "prohibited"},
    "epistemic": {"asserted", "estimated", "doubted", "denied"},
    "modality": {"possible", "probable", "conditional"},
    "polarity": {"negated"},
}

QualifierTypeV4 = Literal[
    "phase", "intention", "authorization", "directive", "epistemic", "modality", "polarity"
]


class EventQualifierV4(StrictModel):
    id: str = Field(..., pattern=r"^Q[1-9][0-9]*$")
    type: QualifierTypeV4
    value: str = Field(..., min_length=1)
    by: list[str] | None = None
    scope: str = Field(..., pattern=r"^(event|Q[1-9][0-9]*)$")
    time: EventTimeExpressionV4 | None = None
    surface: str | None = None

    @model_validator(mode="after")
    def check_qualifier(self):
        if self.value not in QUALIFIER_VALUES_V4[self.type]:
            raise ValueError(f"invalid value '{self.value}' for qualifier type '{self.type}'")
        if self.type == "epistemic" and not self.by:
            raise ValueError("by is required for epistemic qualifier")
        if self.type in {"phase", "polarity"} and self.by:
            raise ValueError(f"by is not allowed for qualifier type '{self.type}'")
        return self


class EventRelationV4(StrictModel):
    predicate: Literal[
        "causes", "promotes", "prevents", "aggravates", "mitigates",
        "precedes", "follows", "overlaps", "condition_for", "part_of",
    ]
    target_event_id: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    surface: str | None = None


FALLBACK_ROLES = {
    "intrinsic": ({"subject"}, {"subject"}),
    "relational": ({"subject", "counterpart"}, {"subject", "counterpart"}),
    "targeted": ({"actor", "target"}, {"actor", "target", "instrument"}),
    "transfer": ({"theme"}, {"theme", "source", "destination", "agent"}),
}


class EventCoreV4(StrictModel):
    frame: EventFrameV4
    predicate: EventPredicateV4
    roles: dict[str, list[str]] = Field(..., min_length=1)
    time: EventTimeV4 | None = None
    context: EventContextV4 | None = None
    attributes: EventAttributesV4 | None = None

    @model_validator(mode="after")
    def check_frame_and_roles(self):
        if any(not refs for refs in self.roles.values()):
            raise ValueError("each role must reference at least one entity")

        if self.predicate.id is None:
            required, allowed = FALLBACK_ROLES[self.frame.topology]
            if not required.issubset(self.roles):
                raise ValueError("unclassified event is missing fallback roles")
            if not set(self.roles).issubset(allowed):
                raise ValueError("unclassified event contains non-fallback roles")
            if self.frame.topology == "transfer" and not ({"source", "destination"} & set(self.roles)):
                raise ValueError("unclassified transfer requires source or destination")
            return self

        spec = PREDICATE_SPECS[self.predicate.id]
        expected_frame = tuple(spec["frame"])
        actual_frame = (self.frame.dynamics, self.frame.topology, self.frame.agency)
        if actual_frame != expected_frame:
            raise ValueError(f"frame does not match predicate {self.predicate.id}")

        required = set(spec["required_roles"])
        allowed = required | set(spec["optional_roles"])
        if not required.issubset(self.roles):
            raise ValueError(f"predicate {self.predicate.id} is missing required roles")
        if not set(self.roles).issubset(allowed):
            raise ValueError(f"predicate {self.predicate.id} contains unsupported roles")
        if self.predicate.id in {"connected_to", "negotiate", "agree", "cooperate", "armed_conflict"}:
            role = "participant" if self.predicate.id == "connected_to" else (
                "belligerent" if self.predicate.id == "armed_conflict" else "party"
            )
            if len(self.roles[role]) < 2:
                raise ValueError(f"predicate {self.predicate.id} requires at least two {role} entities")
        if self.predicate.id == "move" and not ({"source", "destination"} & set(self.roles)):
            raise ValueError("move requires source or destination")
        if self.predicate.id == "trade" and not ({"buyer", "seller"} & set(self.roles)):
            raise ValueError("trade requires buyer or seller")
        return self


class IntelligenceEventV4(StrictModel):
    id: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    core: EventCoreV4
    qualifiers: list[EventQualifierV4] = Field(default_factory=list, max_length=8)
    relations: list[EventRelationV4] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_qualifiers(self):
        scopes: dict[str, str] = {}
        for qualifier in self.qualifiers:
            if qualifier.id in scopes:
                raise ValueError(f"duplicate qualifier id: {qualifier.id}")
            if qualifier.scope != "event":
                if qualifier.scope not in scopes:
                    raise ValueError(f"qualifier scope must reference an earlier qualifier: {qualifier.scope}")
                if scopes[qualifier.scope] != "event":
                    raise ValueError("qualifier nesting cannot exceed one level")
            scopes[qualifier.id] = qualifier.scope
        return self


MainTaxonomy = Literal["政治与安全", "经济与金融", "科技与网络", "社会与环境"]
SubCategory = Literal[
    "国际博弈", "国内政局", "国防军事", "法律与合规", "战略认知", "重大犯罪与恐怖主义",
    "宏观经济", "商业与市场", "能源与资源", "交通与物流", "农业与粮食",
    "前沿科技", "信息安全", "数字基础设施",
    "社会民生", "公共卫生", "自然灾害与环境", "教育与文化",
]

TAXONOMY_SUBCATEGORIES = {
    "政治与安全": {"国际博弈", "国内政局", "国防军事", "法律与合规", "战略认知", "重大犯罪与恐怖主义"},
    "经济与金融": {"宏观经济", "商业与市场", "能源与资源", "交通与物流", "农业与粮食"},
    "科技与网络": {"前沿科技", "信息安全", "数字基础设施"},
    "社会与环境": {"社会民生", "公共卫生", "自然灾害与环境", "教育与文化"},
}


class IntelligenceRateV4(StrictModel):
    impact_scope: int = Field(..., alias="影响广度", ge=1, le=10)
    impact_severity: int = Field(..., alias="影响深度", ge=1, le=10)
    novelty: int = Field(..., alias="新颖性与异常性", ge=1, le=10)
    evolution: int = Field(..., alias="演化与连锁潜力", ge=1, le=10)
    sentiment: int = Field(..., alias="舆情及认知影响", ge=1, le=10)
    actionability: int = Field(..., alias="可行动性", ge=1, le=10)


class ValuableIntelligenceV4(StrictModel):
    EVENT_SCHEMA_VERSION: Literal["4.0"]
    PRIMARY_EVENT_ID: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    ENTITIES: list[EventEntityV4] = Field(..., max_length=64)
    EVENTS: list[IntelligenceEventV4] = Field(..., min_length=1, max_length=3)

    EVENT_TITLE: str = Field(..., min_length=1, max_length=30)
    EVENT_BRIEF: str = Field(..., min_length=1, max_length=100)
    EVENT_TEXT: str = Field(..., min_length=1, max_length=2000)

    TAXONOMY: MainTaxonomy
    SUB_CATEGORY: list[SubCategory] = Field(..., min_length=1, max_length=5)
    IMPACT: str = Field(..., min_length=1, max_length=100)
    REASON: str = Field(..., min_length=1, max_length=100)
    RATE: IntelligenceRateV4
    TIPS: str = Field(..., max_length=100)

    @model_validator(mode="after")
    def check_record(self):
        if not (set(self.SUB_CATEGORY) & TAXONOMY_SUBCATEGORIES[self.TAXONOMY]):
            raise ValueError("at least one SUB_CATEGORY must belong to TAXONOMY")

        entity_ids = [entity.id for entity in self.ENTITIES]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("ENTITIES contains duplicate IDs")
        entity_id_set = set(entity_ids)

        event_ids = [event.id for event in self.EVENTS]
        event_id_set = set(event_ids)
        if len(event_ids) != len(event_id_set):
            raise ValueError("EVENTS contains duplicate IDs")
        if self.PRIMARY_EVENT_ID not in event_id_set:
            raise ValueError("PRIMARY_EVENT_ID must reference EVENTS")

        for event in self.EVENTS:
            references = [ref for refs in event.core.roles.values() for ref in refs]
            if event.core.context:
                references.extend(event.core.context.event_location)
            for qualifier in event.qualifiers:
                references.extend(qualifier.by or [])
            missing = sorted(set(references) - entity_id_set)
            if missing:
                raise ValueError(f"event {event.id} references unknown entities: {missing}")
            for relation in event.relations:
                if relation.target_event_id == event.id:
                    raise ValueError(f"event {event.id} cannot relate to itself")
                if relation.target_event_id not in event_id_set:
                    raise ValueError(f"event {event.id} references unknown event")
        return self


class NonIntelligenceV4(StrictModel):
    TAXONOMY: Literal["无情报价值"]
    REASON: str = Field(..., min_length=1, max_length=100)


AnalysisResultV4 = ValuableIntelligenceV4 | NonIntelligenceV4
ANALYSIS_RESULT_V4_ADAPTER = TypeAdapter(AnalysisResultV4)


def validate_analysis_result_v4(data: Any) -> AnalysisResultV4:
    return ANALYSIS_RESULT_V4_ADAPTER.validate_python(data)


def event_v4_json_schema() -> dict[str, Any]:
    return ANALYSIS_RESULT_V4_ADAPTER.json_schema()