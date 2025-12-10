import datetime
from mongodb_exporter import export_mongodb_data


if __name__ == "__main__":
    # 生成带时间戳的文件名
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"intelligence_archived_{timestamp}.json"

    # 导出 IntelligenceIntegrationSystem 数据库中的 intelligence_archived 集合所有记录
    export_mongodb_data(
        uri="mongodb://localhost:27017",  # 根据实际情况修改连接字符串
        db="IntelligenceIntegrationSystem",
        collection="intelligence_archived",
        output_file=output_file,
        export_format="json"  # 使用JSON格式确保兼容mongoimport
    )


"""
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_cached --file=intelligence_cached.json
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_archived --file=intelligence_archived.json
"""
