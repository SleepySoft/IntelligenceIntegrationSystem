# 首先安装库： pip install json-repair
import json
import json_repair


# 示例：包含未转义引号的错误JSON
bad_json_str = """{"UUID":"6ec8291a-9402-4ad2-8550-5b5e01865a3c","INFORMANT":"https://www.cbc.ca/news/canada/thunder-bay/superior-shoal-thunder-bay-9.6933006?cmp=rss","PUB_TIME":"2025-10-11","TIME":["2025-09"],"LOCATION":["苏必利尔湖","苏必利尔浅滩","加拿大","安大略省","桑德贝","明尼苏达州德卢斯"],"PEOPLE":["Michael Rennie","Yvonne Drebert","Zach Melnich"],"ORGANIZATION":["Lakehead University","International Institute for Sustainable Development-Experimental Lakes Area","Inspired Planet Productions","Royal Canadian Geographical Society","Boxfish Robotics","TVOntario"],"EVENT_TITLE":"加美联合科考苏必利尔浅滩","EVENT_BRIEF":"Lakehead大学团队乘"蓝鹭"号对苏必利尔湖中心火山岩浅滩进行9日科考，采集生态数据并拍摄稀有红鳍湖鳟。","EVENT_TEXT":"苏必利尔浅滩是一座完全淹没于苏必利尔湖中心、距岸70公里、高约300米的水下火山岩山。1929年首次被测绘，现处世界最大淡水保护区。2025年9月初，Lakehead大学Michael Rennie率6人科研组与加拿大地理学会会士Drebert、Melnich纪录片团队，自明尼苏达德卢斯乘83英尺"蓝鹭"号调查船，对浅滩进行9天综合考察。项目获加政府资助，旨在厘清水流、光照等物理过程与生物群落的耦合机制，评估浅滩对湖鳟渔业的支撑作用。团队使用500米级ROV拍摄到红鳍、lean、siscowet等稀有湖鳟品系，并记录其利用上升流"滑翔"行为。数据将用于比较1960-70年代渔业崩溃前后基因差异，并推动将淡水海山纳入保护议程。拍摄素材计划2027年前后通过TVOntario系列片《Hidden Below: the Freshwater World》及配套科学纪录片发布。","RATE":{"国家政策":0,"国际关系":2,"政治影响":0,"商业金融":0,"科技信息":3,"社会事件":0,"其它信息":4,"内容准确率":8,"规模及影响":2,"潜力及传承":3},"IMPACT":"科技信息维度最高：淡水海山生态机制首次系统观测，具区域科研价值，但影响限于学术与保护议题。","TIPS":"研究尚处数据整理阶段，后续论文与政策建议值得跟踪。"}"""


try:
    # 尝试使用标准库解析，预期会失败
    data = json.loads(bad_json_str)
except json.JSONDecodeError as e:
    print(f"标准库解析失败: {e}")
    print("开始尝试修复...")
    # 使用 json_repair 修复
    repaired_data = json_repair.loads(bad_json_str)
    print("修复成功！")
    print(f"修复后的数据: {repaired_data}")
    # 如果需要，可以再转回JSON字符串
    fixed_json_str = json.dumps(repaired_data, ensure_ascii=False, indent=4)
    print(f"规范的JSON字符串: {fixed_json_str}")

    try:
        data = json.loads(fixed_json_str)
        print(f"修复成功")
    except json.JSONDecodeError as e:
        print(f"依然解析失败: {e}")
