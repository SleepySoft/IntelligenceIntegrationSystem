# 情报整合系统 - Intelligence Integration System (IIS) v4

## 链接

[v4版本测试运行中](/views)

## v4版本说明

v4版本相对于v2是一个质变。

在v2版本里系统仅机械提取情报的5要素，但它回答不了以下问题：

1. 如果我想在地图上标记地该新闻的地点，我应该选择哪个？
2. 不同实体在这个事件中是什么角色，施受方向如何？
3. 情报之间如何关联成线成图？仅靠向量？还是靠实体交集？

在v4版本中，地域视图和实体视图的实现更自然也更清晰，情报表述也进化为“可计算”的。

v2版本在main分支，和v4版本不兼容。

## 说明

该系统用以收集国内外主流媒体的公开信息，通过AI进行分类、评分、翻译，旨在筛除无价值信息，高效整合全球公开情报。

本系统属于公开来源情报 (Open-source intelligence，OSINT) 的一个实践，当前通过RSS采集新闻以避免潜在的法律问题。

本项目为开源项目，项目地址：[Intelligence Integration System](https://github.com/SleepySoft/IntelligenceIntegrationSystem/tree/SubSystem)

系统当前为测试状态，不能保证————但会尽量做到————7 x 24小时在线。

请勿尝试抓取本网站数据以免增加系统负担，因为我会定时导出数据并供直接下载，你也可以拉取代码并自行部署本系统，故没有抓取的必要。

## 声明

所有情报均来源于媒体发布信息，不代表本人立场。据我观察，某些国外媒体（特别是德国之声，dw）的新闻较为反华，请仔细鉴别。

情报的原始来源如果不使用梯子很有可能打不开，理由大概率因为上面一条。

注意：情报分类中的“本国”和“国内”并不特指中国。为了保证情报分析的通用性，所以并没有加入特定国别的判定。

## 数据下载

+ [自动备份与上传](https://pan.baidu.com/s/1Fpf32ZJAVITglTAqKkH1GQ?pwd=yucs)

+ [不定期手工导出](https://pan.baidu.com/s/122mewzpNkd6A8UjMDpIMsg?pwd=tfx7)

> 注：由于度盘会员到期，所以v2版本的完整数据未能上传。等我找机会续了会员再说。

数据可通过MongoDB的mongoimport工具导入：

```
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_cached --file=intelligence_cached.json
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_archived --file=intelligence_archived.json
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_low_value --file=intelligence_low_value.json
```

## 赞助该项目

本项目在情报处理上会耗费大量的Token，我正在寻找合适的服务提供商。

如果您是AI服务提供商，并且愿意为本项目提供算力，请联系我（联系方式在github说明文本中）。

免费的赞助方式如下：

+ 硅基流动赠金：
> 使用邀请链接：https://cloud.siliconflow.cn/i/ml9II4B7 或 邀请码：ml9II4B7 进行注册。

+ Mimo体验金
> 使用邀请链接：https://platform.xiaomimimo.com?ref=38LD4J 或 邀请码：38LD4J 进行注册。

+ Gemini的免费额度
> 在Google AI Studio中创建一个免费结算额度的账号，把Token共享给我。

谢谢。
