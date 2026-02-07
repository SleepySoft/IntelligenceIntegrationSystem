# 情报整合系统 - Intelligence Integration System (IIS)

## 链接

[点此查看情报列表](/intelligences?offset=0&count=20&threshold=6)

> 20260207: 
> 
> 之前由于服务器硬盘损坏导致长时间下线，现在从另一台电脑拆了块硬盘先用着。坏消息是，这块硬盘和之前坏的硬盘是同一型号，所以不排除再次发生同样的事情。。。
> 
> IIS已切换到v2版本的情报分析模式，v2版本的代码将于2月15日正式成为main分支。
> 
> 不知道是由于prompt原因或者是AI服务问题，尽管我强调输出必须为中文，但分析结果依然可能保留原始语言。在情报列表页可以查看情报对应的分析Prompt及使用的AI服务，供大家参考。

## 说明

该系统用以收集国内外主流媒体的公开信息，通过AI进行分类、评分、翻译，旨在筛除无价值信息，高效整合全球公开情报。

本系统属于公开来源情报 (Open-source intelligence，OSINT) 的一个实践，当前通过RSS采集新闻以避免潜在的法律问题。

本项目为开源项目，项目地址：[Intelligence Integration System](https://github.com/SleepySoft/IntelligenceIntegrationSystem/tree/dev)

系统当前为测试状态，不能保证————但会尽量做到————7 x 24小时在线。

请勿尝试抓取本网站数据以免增加系统负担，因为我会定时导出数据并供直接下载，你也可以拉取代码并自行部署本系统，故没有抓取的必要。

## 声明

所有情报均来源于媒体发布信息，不代表本人立场。据我观察，某些国外媒体（特别是德国之声，dw）的新闻较为反华，请仔细鉴别。

情报的原始来源如果不使用梯子很有可能打不开，理由大概率因为上面一条。

注意：情报分类中的“本国”和“国内”并不特指中国。为了保证情报分析的通用性，所以并没有加入特定国别的判定。

## 数据下载

+ [自动备份与上传](https://pan.baidu.com/s/1Fpf32ZJAVITglTAqKkH1GQ?pwd=yucs)

+ [不定期手工导出](https://pan.baidu.com/s/122mewzpNkd6A8UjMDpIMsg?pwd=tfx7)

数据可通过MongoDB的mongoimport工具导入：

```
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_cached --file=intelligence_cached.json
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_archived --file=intelligence_archived.json
mongoimport --uri=mongodb://localhost:27017 --db=IntelligenceIntegrationSystem --collection=intelligence_low_value --file=intelligence_low_value.json
```

## 赞助该项目

本项目使用硅基流动提供的AI服务。如果你能通过我的邀请链接注册，那么我的账户将会获得14元赠金，为该系统增加约半天的AI分析额度。

邀请链接：https://cloud.siliconflow.cn/i/ml9II4B7

或邀请码：ml9II4B7

如果您愿意支持更多，可以在闲鱼搜索“硅基流动赠金”，并将上面的邀请链接提供给商家。

如果您是AI服务提供商，愿意为本项目提供算力，请联系我（联系方式在github说明文本中）。

谢谢。
