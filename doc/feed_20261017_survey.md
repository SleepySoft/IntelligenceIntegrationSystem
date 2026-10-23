# 数据源普查与新源调研（2026-10-17）

> 背景：多个 RSS 源已死或退化；本报告审计现有源活性，并实测可免费抓取的
> 高价值"列表页"式新源（分类至少覆盖 国际/政治/经济）。
> 方法：治理库 `_data/spider_governance.db` 离线审计 + 代理实网探测
> （列表页 HTTP/链接数 + 抽 2 篇文章页验证正文长度）。
> 原始数据：`_data/source_probe_result.json`、`_data/source_probe_round2.json`。

---

## 一、现有源死源清单

| 源 | 状态 | 证据 | 建议 |
|---|---|---|---|
| **voanews** | **源已死** | VOA 2025-03 被关停，feed 内容冻结于 2025-03-13/14；每轮 20 条全是旧文被去重跳过；Hub 侧零记录（受旧应答 bug 影响，2 月那批实际未入库） | **停用** `task_crawl_voanews.py` |
| **investing.com** | **事实死亡** | 47993 条 "Got empty content at fetch"（反爬/付费墙），近 3 天 0 成功 | **停用**或降级为标题源 |
| **aa (Anadolu)** | **静默** | crawl_log 自 2026-04-06 零记录；任务能启动但无产出（列表页结构可能已变） | 待修或停用；修前先看列表页是否还有效 |
| **nhk/cat*.xml、ntv/*.rss** | 历史死配置 | 2026-02-14 后零活动，已被非 .xml/.rss 新配置替代 | 从任务配置中清理，避免污染治理统计 |
| **elpais** | **退化** | 10288 条 "Got empty content at fetch"，但"最新消息"组仍有产出 | 保留观察；若持续恶化按 investing 处理 |
| **ntv/ekonomi、ntv/gundem、dw/Science、news_cn/中央文件** | 低产观察 | feed 正常但近 3 天零新文章 | 观察；可能是栏目低频或 feed 停更 |

仍健康的源：chinanews、news_cn、aljazeera、bbc、abc、cbc、dw（主栏目）、france24、
rfi、tass、nhk（新 cat 配置）、yna、ntv（turkiye/dunya）。

---

## 二、新源实测结果

### ✅ 推荐接入（列表页 + 文章页均验证可抓，免费）

| 源 | 角度 | 国际 | 政治 | 经济 | 实测 |
|---|---|---|---|---|---|
| **The Guardian** | 英国/欧洲左翼 | `/world` | `/politics` | `/uk/business` | 列表 200/30-98 链接；正文 6-21k 字符。另有 `{栏目}/rss` 可用 |
| **Euronews** | 欧盟 | `/news/international` | `/news/europe` | `/business` | 列表 200/163-330 链接；正文 9-11k。量最大 |
| **AFP** | 法新社电讯 | `/en/news-hub`（综合） | — | — | 200/29 链接；正文 8k。快讯事实源，利于交叉验证 |
| **Moscow Times** | 俄罗斯独立视角 | `/news` | — | `/business` | 200/22-52；正文 6-7k。与 TASS 形成对冲 |
| **Times of India** | 印度 | `/world` | `/india`（国内政治） | `/business` | 200/226-397；正文 16-19k |
| **Straits Times** | 新加坡/东南亚 | `/world` | — | `/business` | 200/26-63；正文 3-5k（部分文章可能截断，需观察） |
| **Jakarta Post** | 印尼/东南亚 | `/world` | — | `/business` | 200/57-199；正文 9-11k |
| **Asahi（AJW 英文）** | 日本 | `/ajw/`（综合） | `/ajw/politics/` | — | 200/14-56；正文 5-8k。与 NHK 互补 |
| **Kyodo（共同社英文）** | 日本 | `/news/world` | — | — | 200/20；正文 6k。注：无 .html 后缀，政治/经济分类页不存在 |
| **Africanews** | 非洲 | `/news/` | — | `/business/` | 200/48-64；正文 6k。现有源完全没有非洲视角 |
| **UN News** | 联合国官方 | `/en/`（综合） | — | — | 200/34-92；正文 5-6k。人道/冲突官方口径 |
| **SCMP** | 香港/涉华 | `/news/world` | — | `/business` | 200/51-91；正文 6-8k（有计量付费墙，正文可能截断，先观察） |

### ⚠️ 可用但受限

| 源 | 问题 |
|---|---|
| TRT World | 列表页 JS 渲染，静态只有 6 链接；文章页可抓。需要 Playwright 或找静态栏目页 |
| Kyiv Independent | 全站 JS 渲染，静态提取 0 链接；需要浏览器渲染 |
| Globes | 列表页可抓，但文章正文仅 0.7-1.2k 字符（付费墙截断） |

### ❌ 放弃（反爬 403/401，按"不给抓就算"原则）

Reuters（401）、AP News（403）、Mainichi（403）、Arab News（403）、
Al Arabiya（403）、The Hindu（403）、Times of Israel（403）。

> 注：Reuters/AP 在 `doc/feed_20260324.md` 中曾列为 P1，但现在 RSS 与列表页均被
> 反爬拦截，除非将来愿意上浏览器渲染链路，否则跳过。

---

## 三、接入建议

1. **第一梯队**（角度互补最强 + 验证最稳）：
   - Guardian（西欧左翼，三类齐全，量足）
   - Euronews（欧盟官方倾向，量最大）
   - Moscow Times（对冲 TASS 的俄方独立视角）
   - Times of India（南亚大国，现有源空白）
   - Africanews（非洲，现有源空白）
2. **第二梯队**：AFP（电讯快讯对照）、Asahi/Kyodo（日本补强 NHK）、
   Straits Times / Jakarta Post（东南亚）、UN News（官方口径）、SCMP（涉华观察）。
3. **接入方式**：全部是列表页发现 + 文章页抓取（IntelligenceCrawler 的
   crawler_config 模式，参照 `crawler_config_aa.py`）；Guardian 也可直接用
   `{栏目}/rss` 走 RSS 流程。
4. **顺手清理**：停用 voanews / investing，清理 nhk `cat*.xml` 与 ntv `*.rss`
   历史配置；aa 的列表页需单独排查（4 月起静默）。
5. 修复后的提交契约（rejected+reason）会让上述所有源的"服务端拒绝"在治理台
   直接可见，不会再出现 voanews 式假成功。

### 2026-10-18 VOA 恢复复查

VOA 主英语栏目 RSS 仍冻结在 2025-03，但官方 RSS 目录中的 Dari、Kurdish、Korean、
Mandarin、Pashto、Persian、Russian、Ukrainian 英文译稿流持续更新，最新文章日期为
2026-10-15/16，正文页面也仍兼容现有提取器。`task_crawl_voanews.py` 已切换到这些
活跃流，并增加 14 天新鲜度检查；部署配置重新启用该任务。

### 2026-10-23 以色列本地媒体补充

为补足中东事件中的以色列本地视角，实测并接入两个英文媒体源：

| 源 | 发现方式 | 实测 | 结论 |
|---|---|---|---|
| Jerusalem Post | 官方 Israel / Politics / Middle East RSS | 每个 Feed 约 30 条；抽样正文约 3.6k–7.6k 字符 | 接入；URL 白名单排除 opinion 等非目标栏目 |
| i24NEWS English | Israel / Elections / Middle East 列表页 | 每页约 24 条；抽样正文约 2.6k–9.9k 字符 | 接入；用精确文章选择器与同站白名单 |
| Haaretz English | 官方 headlines RSS | Feed 约 100 条，但抽样正文仅约 330 字符 | 暂缓；付费墙摘要不足以进入 AI 主链 |

本轮只接新闻媒体，不接 IDF、外交部等官方声明。官方声明需要先补充来源类型、主张者、
独立来源计数和前端标识，避免把单方声明直接显示为已证实事实。

### 2026-10-23 东南亚与南美最小补源

考虑到国际政治主链仍由少数关键地区和媒体承担，本轮不追求逐国铺满，只采用“区域主源 +
主要国家本地源”的小组合，并主动排除体育、生活、观点及全量 Latest Feed：

| 区域 | 源 | 接入口径 | 上线前实测 |
|---|---|---|---|
| 东南亚 | CNA Asia | 仅官方 Asia RSS；排除 commentary/sport/lifestyle/watch | 发现 20 条，URL 过滤后 12 条；抽样正文 2.8k 字符 |
| 东南亚 | Jakarta Post | 仅 Indonesia 列表中的规范 Indonesia/Business URL | 7 条；抽样正文 2.3k–3.0k 字符 |
| 南美 | MercoPress | 仅 Latin America RSS | 10 条；抽样正文约 2.4k 字符 |
| 南美 | Agência Brasil English | 仅政治、经济、国际、司法、环境英文栏目 | 8 条；抽样正文约 3.2k 字符 |

CNA RSS 条款限定个人、非商业使用；部署用途变化时需重新审查。Agência Brasil 只接低流量
英文页，不接葡萄牙语全量 Feed，避免巴西单一来源淹没南美其它国家信号。
