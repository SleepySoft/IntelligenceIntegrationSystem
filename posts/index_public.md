# 情报整合系统 - Intelligence Integration System (IIS)

## 链接

[点此查看情报列表](/intelligences?offset=0&count=20&threshold=6)

[AI推荐24小时内最有价值的不超过50条情报（每小时更新一次，测试）](/recommendations)

> 20251129： 最近由于更换AI服务的架构，导致消息分析及更新不及时，同时随时有可能下线调试，请见谅。


## 说明

该系统用以收集国内外主流媒体的公开信息，通过AI进行分类、评分、翻译，旨在筛除无价值信息，高效整合全球公开情报。

本系统属于公开来源情报 (Open-source intelligence，OSINT) 的一个实践，当前通过RSS采集新闻以避免潜在的法律问题。

本项目为开源项目，项目地址：[Intelligence Integration System](https://github.com/SleepySoft/IntelligenceIntegrationSystem/tree/dev)

系统当前为测试状态，不能保证————但会尽量做到————7 x 24小时在线。

请勿尝试抓取本网站数据以免增加系统负担，因为我会定时导出数据并供直接下载，你也可以拉取代码并自行部署本系统，故没有抓取的必要。

## 声明

所有情报均来源于媒体发布信息，不代表本人立场。据我观察，某些国外媒体（特别是德国之声，dw）的新闻较为反华，请仔细鉴别。

情报的原始来源如果不使用梯子很有可能打不开，理由大概率因为上面一条。

## 数据下载

通过MongoDB的mongoimport工具导入：

```mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_archived --file=intelligence_archived.json```

+ [自动备份与上传](https://pan.baidu.com/s/1Fpf32ZJAVITglTAqKkH1GQ?pwd=yucs)

## 赞助该项目

本项目使用硅基流动提供的AI服务。如果你能通过我的邀请链接注册，那么我的账户将会获得14元赠金，为该系统增加约半天的AI分析额度。

邀请链接：https://cloud.siliconflow.cn/i/ml9II4B7

或邀请码：ml9II4B7

如果您愿意支持更多，可以在闲鱼搜索“硅基流动赠金”，并将上面的邀请链接提供给商家。

谢谢。
