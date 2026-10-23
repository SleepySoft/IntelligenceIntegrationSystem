"""生成供单文件接入示例使用的多主题 Event File v1 数据集。"""

from __future__ import annotations

import json
from pathlib import Path


OUTPUT = Path(__file__).parent / "data" / "multitopic_events.json"
entities: dict[str, dict] = {}
events: list[dict] = []


def entity(key: str, name: str, entity_type: str, country_code: str | None = None) -> str:
    value = {"id": key, "name": name, "type": entity_type}
    if country_code:
        value["country_code"] = country_code
    previous = entities.setdefault(key, value)
    if previous != value:
        raise ValueError(f"实体定义冲突: {key}")
    return key


def add_event(
    topic: str,
    predicate_id: str,
    surface: str,
    frame: tuple[str, str, str],
    roles: dict[str, list[str]],
    day: str,
    *,
    locations: tuple[str, ...] = (),
    phase: str | None = None,
    attributes: dict | None = None,
    relations: list[dict] | None = None,
) -> None:
    number = len(events) + 1
    value = {
        "id": f"EV{number:02d}",
        "intelligence_id": f"INTEL{number:02d}",
        "topic": topic,
        "frame": {"dynamics": frame[0], "topology": frame[1], "agency": frame[2]},
        "predicate": {"id": predicate_id, "surface": surface},
        "roles": roles,
        "time": {
            "event_time": {
                "normalized": day,
                "precision": "day",
                "approximate": False,
                "surface": day,
            }
        },
        "locations": list(locations),
        "observed_at": f"{day}T12:00:00+08:00",
        "is_primary": True,
    }
    if phase:
        value["qualifiers"] = [
            {"id": "Q1", "type": "phase", "value": phase, "scope": "event"}
        ]
    if attributes:
        value["attributes"] = attributes
    if relations:
        value["relations"] = relations
    events.append(value)


POLITICAL = ("change", "relational", "agentive")
TARGETED_CHANGE = ("change", "targeted", "agentive")
TARGETED_PROCESS = ("process", "targeted", "agentive")
TRANSFER_CHANGE = ("change", "transfer", "agentive")
TRANSFER_PROCESS = ("process", "transfer", "agentive")
INTRINSIC_CHANGE = ("change", "intrinsic", "unknown")
HAZARD = ("process", "intrinsic", "non_agentive")

# 政治、外交和法律
add_event("总统选举", "elect", "当选", POLITICAL,
          {"electorate": [entity("voters_a", "A国选民", "organization", "US")],
           "person": [entity("president_a", "阿列克斯·摩根", "person", "US")],
           "position": [entity("presidency_a", "A国总统", "position", "US")]}, "2026-01-10", phase="completed")
add_event("金融制裁", "sanction", "实施制裁", TARGETED_CHANGE,
          {"authority": [entity("country_b", "B国政府", "geopolitical_entity", "GB")],
           "target": [entity("bank_x", "远洋银行", "organization", "RU")]}, "2026-01-14", phase="completed")
add_event("边境停火协议", "agree", "签署停火协议", POLITICAL,
          {"party": [entity("country_c", "C国", "geopolitical_entity", "IN"), entity("country_d", "D国", "geopolitical_entity", "PK")],
           "agreement": [entity("border_ceasefire", "北部边境停火协议", "agreement")]}, "2026-01-22", phase="completed")
add_event("首都反腐抗议", "protest", "举行抗议", TARGETED_PROCESS,
          {"participant": [entity("civic_group", "公民行动联盟", "organization")],
           "target": [entity("cabinet_c", "C国内阁", "organization", "IN")]}, "2026-02-03",
          locations=(entity("capital_c", "C国首都", "location", "IN"),), phase="ongoing")
add_event("公共采购调查", "investigate", "启动调查", TARGETED_PROCESS,
          {"investigator": [entity("audit_office", "国家审计署", "organization")],
           "object": [entity("rail_contract", "高速铁路采购案", "case")]}, "2026-02-08", phase="ongoing")
add_event("跨境嫌犯拘捕", "detain", "拘捕", TARGETED_CHANGE,
          {"authority": [entity("federal_police", "联邦警察局", "organization")],
           "person": [entity("suspect_k", "K某", "person")]}, "2026-02-12", phase="completed")
add_event("海洋划界谈判", "negotiate", "举行谈判", ("process", "relational", "agentive"),
          {"party": [entity("country_e", "E国", "geopolitical_entity", "ID"), entity("country_f", "F国", "geopolitical_entity", "MY")],
           "topic": [entity("sea_boundary", "南部海域划界", "topic")]}, "2026-02-19", phase="ongoing")
add_event("最高法院反垄断裁决", "adjudicate", "作出裁决", TARGETED_CHANGE,
          {"authority": [entity("supreme_court", "最高法院", "organization")],
           "case": [entity("platform_case", "平台反垄断案", "case")],
           "party": [entity("platform_q", "Q平台公司", "organization")]}, "2026-02-25", phase="completed")

# 军事与安全
add_event("边境哨所袭击", "attack", "炮击", ("process", "targeted", "agentive"),
          {"actor": [entity("armed_group", "北境武装组织", "organization")],
           "target": [entity("border_post", "第七码边境哨所", "facility")],
           "instrument": [entity("artillery", "火炮", "equipment")]}, "2026-03-02",
          locations=(entity("north_border", "北部边境", "location"),), phase="completed")
add_event("防空导弹拦截", "intercept", "拦截", ("process", "targeted", "agentive"),
          {"actor": [entity("air_defense", "东部防空部队", "organization")],
           "target": [entity("incoming_missile", "来袭巡航导弹", "equipment")],
           "instrument": [entity("sam_system", "远程防空系统", "equipment")]}, "2026-03-05",
          locations=(entity("east_city", "东部工业城", "location"),), phase="completed")
add_event("北方舰队部署", "deploy", "部署", ("change", "targeted", "agentive"),
          {"deployer": [entity("navy_g", "G国海军", "organization", "JP")],
           "object": [entity("destroyer_group", "驱逐舰编队", "equipment")],
           "location": [entity("north_sea", "北方海域", "location")]}, "2026-03-11", locations=("north_sea",), phase="completed")
add_event("多国联合演训", "cooperate", "开展联合演训", ("process", "relational", "agentive"),
          {"party": [entity("army_h", "H国陆军", "organization", "DE"), entity("army_i", "I国陆军", "organization", "FR")],
           "topic": [entity("joint_drill", "山地联合演训", "topic")]}, "2026-03-18",
          locations=(entity("highland_range", "高原训练场", "facility"),), phase="ongoing")

# 经济、金融和供应链
add_event("央行加息", "regulate", "上调政策利率", TARGETED_PROCESS,
          {"authority": [entity("central_bank", "中央银行", "organization")],
           "target": [entity("policy_rate", "基准政策利率", "policy")]}, "2026-04-01", phase="completed",
          attributes={"delta": {"type": "number", "value": 0.25, "unit": "百分点"}})
add_event("电动车企业收购", "acquire", "完成收购", TRANSFER_CHANGE,
          {"acquirer": [entity("auto_group", "新航汽车集团", "organization")],
           "asset": [entity("battery_company", "极光电池公司", "organization")],
           "seller": [entity("holding_m", "M控股", "organization")]}, "2026-04-06", phase="completed",
          attributes={"amount": {"type": "money", "value": 4.2, "currency": "USD", "unit": "billion"}})
add_event("半导体工厂投资", "invest", "投资建设", TRANSFER_PROCESS,
          {"investor": [entity("chip_fund", "先进制造基金", "organization")],
           "recipient": [entity("fab_company", "晶圆制造公司", "organization")],
           "resource": [entity("fab_capital", "建厂资金", "asset")]}, "2026-04-12", phase="not_started",
          attributes={"amount": {"type": "money", "value": 8.0, "currency": "USD", "unit": "billion"}})
add_event("港口扩建融资", "lend", "提供贷款", TRANSFER_CHANGE,
          {"lender": [entity("development_bank", "区域开发银行", "organization")],
           "borrower": [entity("port_authority", "蓝湾港务局", "organization")]}, "2026-04-16", phase="completed",
          attributes={"amount": {"type": "money", "value": 950, "currency": "USD", "unit": "million"}})
add_event("液化天然气长协供应", "supply", "开始供应", TRANSFER_PROCESS,
          {"supplier": [entity("lng_exporter", "北极能源公司", "organization")],
           "recipient": [entity("utility_j", "J国燃气公司", "organization", "KR")],
           "goods": [entity("lng", "液化天然气", "resource")]}, "2026-04-21", phase="ongoing")
add_event("集装箱港口事故", "accident", "起重机倒塌", ("change", "intrinsic", "non_agentive"),
          {"affected": [entity("container_terminal", "蓝湾集装箱码头", "facility")]}, "2026-04-25",
          locations=(entity("blue_port", "蓝湾港", "location"),), phase="completed")
add_event("跨境铁路建设", "construct", "开工建设", TARGETED_PROCESS,
          {"builder": [entity("rail_consortium", "跨境铁路联合体", "organization")],
           "object": [entity("cross_border_rail", "东西跨境铁路", "facility")]}, "2026-05-02", phase="ongoing")
add_event("小麦产量下调", "decrease", "下调产量预期", INTRINSIC_CHANGE,
          {"subject": [entity("wheat_output", "本年度小麦产量", "resource")]}, "2026-05-07",
          attributes={"ratio": {"type": "ratio", "value": 0.12, "surface": "下调12%"}})

# 科技、航天与网络
add_event("先进制程芯片研发", "develop", "研发", TARGETED_PROCESS,
          {"developer": [entity("micro_lab", "微电子联合实验室", "organization")],
           "object": [entity("two_nm_chip", "2纳米试验芯片", "product")]}, "2026-05-14", phase="ongoing")
add_event("开源大模型发布", "release", "发布", ("change", "transfer", "agentive"),
          {"releaser": [entity("ai_lab", "星河人工智能实验室", "organization")],
           "object": [entity("open_model", "星河基础模型4", "product")]}, "2026-05-20", phase="completed")
add_event("量子网络测试", "test", "完成测试", TARGETED_PROCESS,
          {"tester": [entity("quantum_institute", "国家量子研究院", "organization")],
           "object": [entity("quantum_network", "城际量子通信网络", "capability")]}, "2026-05-26", phase="completed")
add_event("医院勒索软件攻击", "disrupt", "加密并中断", TARGETED_PROCESS,
          {"actor": [entity("ransomware_group", "黑潮勒索组织", "organization")],
           "target": [entity("hospital_system", "城市医院信息系统", "facility")],
           "instrument": [entity("ransomware", "黑潮勒索软件", "equipment")]}, "2026-06-03", phase="ongoing")
add_event("数据泄露通报", "communicate", "披露", TRANSFER_PROCESS,
          {"sender": [entity("cloud_company", "云桥科技", "organization")],
           "information": [entity("breach_notice", "用户数据泄露通报", "information")]}, "2026-06-07", phase="completed",
          relations=[{"predicate": "follows", "target": "EV24", "surface": "事件发生后发布通报"}])
add_event("遥感卫星部署", "deploy", "部署入轨", ("change", "targeted", "agentive"),
          {"deployer": [entity("space_agency", "国家航天局", "organization")],
           "object": [entity("remote_sat", "海洋遥感卫星", "equipment")],
           "location": [entity("low_orbit", "近地轨道", "location")]}, "2026-06-12", locations=("low_orbit",), phase="completed")
add_event("商业飞船故障", "accident", "推进系统故障", ("change", "intrinsic", "non_agentive"),
          {"affected": [entity("cargo_spacecraft", "远航货运飞船", "equipment")]}, "2026-06-18",
          locations=("low_orbit",), phase="ongoing")

# 公共卫生、环境、能源和民生
add_event("沿海强震", "natural_hazard", "发生地震", HAZARD,
          {"phenomenon": [entity("coastal_quake", "沿海7.1级地震", "phenomenon")],
           "affected_area": [entity("coastal_province", "东部沿海省", "location")]}, "2026-07-01", locations=("coastal_province",), phase="completed",
          attributes={"level": {"type": "number", "value": 7.1, "unit": "级"}})
add_event("河流洪水", "natural_hazard", "暴发洪水", HAZARD,
          {"phenomenon": [entity("river_flood", "大河流域洪水", "phenomenon")],
           "affected_area": [entity("river_basin", "大河中下游", "location")]}, "2026-07-05", locations=("river_basin",), phase="ongoing")
add_event("登革热暴发", "outbreak", "病例暴发", HAZARD,
          {"phenomenon": [entity("dengue", "登革热", "phenomenon")],
           "affected_area": [entity("south_city", "南部都会区", "location")]}, "2026-07-09", locations=("south_city",), phase="ongoing")
add_event("疫苗紧急供应", "supply", "调拨供应", TRANSFER_PROCESS,
          {"supplier": [entity("health_ministry", "卫生部", "organization")],
           "recipient": [entity("south_hospitals", "南部医院联盟", "organization")],
           "goods": [entity("dengue_vaccine", "登革热疫苗", "product")]}, "2026-07-11", phase="ongoing",
          relations=[{"predicate": "mitigates", "target": "EV30", "surface": "用于控制疫情"}])
add_event("重症床位短缺", "shortage", "出现短缺", ("state", "relational", "non_agentive"),
          {"resource": [entity("icu_beds", "重症监护床位", "resource")],
           "affected": ["south_hospitals"]}, "2026-07-14", locations=("south_city",), phase="ongoing")
add_event("山林野火", "natural_hazard", "野火蔓延", HAZARD,
          {"phenomenon": [entity("forest_fire", "西部山林野火", "phenomenon")],
           "affected_area": [entity("west_forest", "西部国家森林", "location")]}, "2026-07-19", locations=("west_forest",), phase="ongoing")
add_event("工业排放限制", "restrict", "限制排放", TARGETED_CHANGE,
          {"authority": [entity("environment_agency", "环境监管署", "organization")],
           "target": [entity("steel_mills", "沿江钢铁企业", "organization")],
           "object": [entity("emission_quota", "氮氧化物排放额度", "policy")]}, "2026-07-24", phase="completed")
add_event("区域干旱", "natural_hazard", "持续干旱", HAZARD,
          {"phenomenon": [entity("drought", "北部农业区干旱", "phenomenon")],
           "affected_area": [entity("north_farms", "北部农业区", "location")]}, "2026-07-29", locations=("north_farms",), phase="ongoing",
          relations=[{"predicate": "causes", "target": "EV20", "surface": "干旱导致产量预期下降"}])
add_event("核电站恢复运行", "restore", "恢复运行", INTRINSIC_CHANGE,
          {"affected": [entity("nuclear_plant", "海岸核电站", "facility")],
           "actor": [entity("grid_operator", "国家电网运营商", "organization")]}, "2026-08-03", phase="completed")

# 商业治理和跨境流动
add_event("加密资产冻结", "seize", "冻结资产", TARGETED_CHANGE,
          {"authority": [entity("financial_unit", "金融情报局", "organization")],
           "object": [entity("crypto_assets", "涉案加密资产", "asset")]}, "2026-08-08", phase="completed",
          attributes={"amount": {"type": "money", "value": 120, "currency": "USD", "unit": "million"}})
add_event("关键矿产出口限制", "restrict", "限制出口", TARGETED_CHANGE,
          {"authority": [entity("trade_ministry", "贸易部", "organization")],
           "target": [entity("foreign_buyers", "境外关键矿产买方", "organization")],
           "object": [entity("rare_minerals", "关键稀有矿产", "resource")]}, "2026-08-13", phase="completed")
add_event("难民跨境转移", "move", "越境进入", ("change", "transfer", "unknown"),
          {"theme": [entity("refugees", "边境难民群体", "organization")],
           "source": [entity("conflict_zone", "冲突地区", "location")],
           "destination": [entity("safe_region", "邻国安全区", "location")]}, "2026-08-18", locations=("safe_region",), phase="ongoing",
          attributes={"quantity": {"type": "number", "value": 18000, "unit": "人"}})
add_event("海底电缆修复", "maintain", "完成修复", TARGETED_PROCESS,
          {"actor": [entity("cable_consortium", "国际海缆维护联盟", "organization")],
           "object": [entity("subsea_cable", "北洋三号海底电缆", "facility")]}, "2026-08-22",
          locations=("north_sea",), phase="completed")
add_event("自由贸易协定生效", "valid", "正式生效", ("state", "intrinsic", "non_agentive"),
          {"subject": [entity("trade_agreement", "区域数字贸易协定", "agreement")]}, "2026-08-28", phase="ongoing")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "event-file/1.0",
        "dataset_id": "iis-event-engine-multitopic-demo-v1",
        "description": "40余个跨领域事件，用于演示 Event Core Engine 的单文件接入、查询和统计。",
        "entities": list(entities.values()),
        "events": events,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(events)} 个事件、{len(entities)} 个实体: {OUTPUT}")


if __name__ == "__main__":
    main()
