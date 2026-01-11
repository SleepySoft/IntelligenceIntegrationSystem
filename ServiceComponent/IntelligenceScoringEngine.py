import pymongo
from typing import Dict, List, Any


class IntelligenceScoringEngine:
    def __init__(self, config: Dict = None):
        """
        初始化评分引擎
        :param config: 配置字典，包含权重和分类系数。如果为 None 则使用默认值。
        """
        self.config = config or self._get_default_config()
        self.weights = self.config.get("weights", {})
        self.multipliers = self.config.get("multipliers", {})

    def _get_default_config(self) -> Dict:
        return {
            "weights": {
                "影响深度": 3.5,
                "影响广度": 3.0,
                "演化潜力": 2.0,
                "舆情及认知影响": 1.0,
                "新颖性与异常性": 0.5,
                "可行动性": 0.0
            },
            "multipliers": {
                "政治与安全": 1.2,
                "经济与金融": 1.1,
                "科技与网络": 1.0,
                "社会与环境": 1.0,
                "无情报价值": 0.0
            }
        }

    def calculate_single(self, intelligence_data: Dict) -> float:
        """
        【Python模式】计算单条数据的分数（用于新数据入库前）
        """
        rates = intelligence_data.get("RATE", {})
        taxonomy = intelligence_data.get("TAXONOMY", "")

        # 1. 基础加权分
        raw_score = 0.0
        for dim, weight in self.weights.items():
            # 容错：如果数据里缺某个维度，默认为 0
            score = float(rates.get(dim, 0))
            raw_score += score * weight

        # 2. 领域加权
        multiplier = self.multipliers.get(taxonomy, 1.0)

        # 3. 计算最终分并保留1位小数
        final_score = round(raw_score * multiplier, 1)

        # 4. 边界处理 (0-100)
        return min(100.0, max(0.0, final_score))

    def get_mongo_update_pipeline(self) -> List[Dict]:
        """
        【命令生成模式】生成 MongoDB Aggregation Pipeline Update 命令
        这是一个允许在 update_many 中使用的管道列表
        """

        # 1. 构建加权求和的表达式 (Weighted Sum)
        # 结果形式: { $add: [ { $multiply: ["$RATE.影响深度", 3.5] }, ... ] }
        weighted_sum_expr = {
            "$add": [
                {
                    "$multiply": [
                        # 使用 $ifNull 防止字段不存在报错
                        {"$ifNull": [f"$RATE.{dim}", 0]},
                        weight
                    ]
                }
                for dim, weight in self.weights.items()
            ]
        }

        # 2. 构建分类系数的 switch-case 表达式
        # 结果形式: { $switch: { branches: [ { case: {$eq: ["$TAXONOMY", "政治"]}, then: 1.2 } ... ], default: 1.0 } }
        branches = []
        for taxonomy, mult in self.multipliers.items():
            branches.append({
                "case": {"$eq": ["$TAXONOMY", taxonomy]},
                "then": mult
            })

        multiplier_expr = {
            "$switch": {
                "branches": branches,
                "default": 1.0
            }
        }

        # 3. 组装最终管道
        pipeline = [
            {
                "$set": {
                    "TOTAL_SCORE": {
                        "$min": [  # 限制最大值为 100
                            100,
                            {
                                "$multiply": [
                                    weighted_sum_expr,
                                    multiplier_expr
                                ]
                            }
                        ]
                    },
                    # 同时记录一下本次计算使用的算法版本或权重哈希，方便以后排查
                    "SCORING_VERSION": "v2.0_dynamic"
                }
            }
        ]
        return pipeline

    def update_database(self, collection: pymongo.collection.Collection, dry_run: bool = False):
        """
        【数据库模式】直接更新 MongoDB 集合中的所有数据
        :param collection: pymongo 的 collection 对象
        :param dry_run: 如果为 True，只打印命令不执行
        """
        pipeline = self.get_mongo_update_pipeline()

        if dry_run:
            print("--- [Dry Run] Generated MongoDB Pipeline ---")
            import json
            print(json.dumps(pipeline, ensure_ascii=False, indent=2))
            return

        # 使用 update_many 配合 pipeline (MongoDB 4.2+ 特性)
        result = collection.update_many({}, pipeline)
        print(f"Update Complete. Matched: {result.matched_count}, Modified: {result.modified_count}")
