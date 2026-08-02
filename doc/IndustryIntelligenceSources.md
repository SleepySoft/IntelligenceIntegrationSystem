# 产业情报源清单与有效性验证报告

> 验证时间：2026-08-02　|　适用：IIS `industry` / `finance` 子系统数据源选型与采集接入
>
> 验证方法：对每个候选源执行真实 HTTP 请求（20–30s 超时，Chrome UA）。页面检查 HTTP 状态、服务端渲染出的链接数量、是否命中 WAF 验证页；RSS/Atom 用 feedparser 解析，记录条目数与最新条目日期。所有结果同时存于 `_export/intel_source_verified.json`（43 条记录），本报告为其汇总。

## 一、结论速览

**第一梯队（可直接接入，更新稳定）**

- RSS 类：36氪 `https://36kr.com/feed`（30 条，最新 2026-08-02）、Supply Chain Dive、Manufacturing Dive、IndustryWeek、SemiEngineering
- 页面类：科创板日报 `chinastarmarket.cn`、OFweek 各频道、集微网首页、CPIA 首页（`chinapv.org.cn`）、中汽协、国家统计局、前瞻产业研究院、艾瑞报告中心、TrendForce、北极星储能频道

**需要特殊处理（反爬/JS 渲染/云建站）**

- 财联社电报（Next.js SPA + 签名 API）、信通院（412 WAF）、亿欧（202 反爬）、北极星主站/光伏频道（间歇 WAF 挑战）、CSIA（万网云建站，正文在 CDN JS 中）、GGII（503，建议走微信/媒体转载渠道）

**几个关键勘误**

- 中国光伏行业协会官网实为 `chinapv.org.cn`，`cpia.org.cn` 当前无法访问，不要用后者。
- C&EN 已停用 RSS（`/rss` 等路径全部 404），只能用栏目页。
- IndustryWeek 的 RSS 不在 `/rss`，实际地址是 Brightspot 的 `__rss/website-scheduled-content.xml` 接口（见下文）。
- 赛迪报告库 `report.ccidgroup.com` 当前 502，不可用；用 `ccidgroup.com/sdyjcg.htm` 替代。

## 二、详细清单

状态标记：✅ 可用（服务端渲染或 RSS 有效）｜⚠️ 有条件可用（需浏览器/API/重试）｜❌ 当前不可直连

### 1. 政府 / 智库

| 站点 | 栏目 / 源 | URL | 类型 | 结果 | 说明 |
|---|---|---|---|---|---|
| 工信部 | 首页 | https://www.miit.gov.cn/ | 页面 | ✅ 200 | 186 个链接，综合动态 |
| 工信部 | 政策文件库（检索页） | https://www.miit.gov.cn/search/wjfb.html?websiteid=110000000000000&pg=&p=&tpl=14&category=51&q= | 页面 | ⚠️ 200 | 标题“工业和信息化部政策文件库”，可按年份/公文种类/机构过滤；结果列表由前端 JS 调用检索接口渲染，抓取需走检索接口或浏览器 |
| 工信部 | 政策文件-文件发布 | https://www.miit.gov.cn/zwgk/zcwj/wjfb/index.html | 页面 | ⚠️ 200 | 壳页，列表 JS 渲染；文章详情页本身是静态 HTML（`/zwgk/zcwj/wjfb/.../art/…html`） |
| 工信部 | 政策文件-通知 | https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/index.html | 页面 | ⚠️ 200 | 同上，本次探测出 19 个链接，内容不完全静态 |
| 工信部 | 工信动态 | https://www.miit.gov.cn/xwdt/gxdt/index.html | 页面 | ⚠️ 200 | 壳页，列表 JS 渲染 |
| 信通院 | 研究成果-白皮书 | https://www.caict.ac.cn/kxyj/qwfb/bps/ | 页面 | ❌ 412 | WAF JS 挑战（urllib 与 curl 均 412），普通脚本无法直连；替代入口：`https://gma.caict.ac.cn/plat/news/full-collection-of-blue-books-and-report-published-by-caict-in-2025`（✅ 200，2025 年“集智”蓝皮书/专题汇总），或微信公众号渠道 |
| 赛迪 | 首页 | https://www.ccidgroup.com/ | 页面 | ✅ 200 | 228 个链接 |
| 赛迪 | 赛迪研究成果 | https://www.ccidgroup.com/sdyjcg.htm | 页面 | ⚠️ 200 | 页面可达但列表由 JS 渲染（9 个静态链接）；`report.ccidgroup.com` 报告库当前 502 不可用 |
| 国家统计局 | 最新发布 | https://www.stats.gov.cn/sj/zxfb/ | 页面 | ✅ 200 | 430 个链接，数据发布正文静态，易抓 |
| 国家统计局 | 数据解读 | https://www.stats.gov.cn/sj/sjjd/ | 页面 | ✅ 200 | 440 个链接 |

### 2. 研究机构

| 站点 | 栏目 / 源 | URL | 类型 | 结果 | 说明 |
|---|---|---|---|---|---|
| 前瞻产业研究院 | 行业研究报告 | https://bg.qianzhan.com/report/ | 页面 | ✅ 200 | 544 个链接，报告/白皮书列表完整 |
| 前瞻产业研究院 | 前瞻趋势 | https://bg.qianzhan.com/trends/ | 页面 | ✅ 200 | 323 个链接，行业趋势解读 |
| 艾瑞咨询 | 首页 | https://www.iresearch.com.cn/ | 页面 | ✅ 200 | 80 个链接 |
| 艾瑞咨询 | 报告中心 | https://report.iresearch.cn/ | 页面 | ✅ 200 | 360 个链接，研究报告库 |
| 亿欧 | 首页 | https://www.iyiou.com/ | 页面 | ❌ 202 | 反爬 JS 挑战，普通请求返回 202 空壳；需浏览器渲染（Playwright）或改走其微信公众号/第三方转载 |
| GGII 高工产业研究院 | 官网 | https://www.gg-ii.com/ | 页面 | ❌ 503/502/超时 | 当前网络环境无法直连（可能 WAF/地域限制或站点迁移）；其锂电/机器人数据建议经高工系微信公号或媒体转载获取 |

### 3. 行业媒体

| 站点 | 栏目 / 源 | URL | 类型 | 结果 | 说明 |
|---|---|---|---|---|---|
| OFweek | 首页 | https://www.ofweek.com/ | 页面 | ✅ 200 | 1070 个链接，高科技综合 |
| OFweek | 电子工程 | https://ee.ofweek.com/ | 页面 | ✅ 200 | 365 个链接 |
| OFweek | 半导体 | https://semi.ofweek.com/ | 页面 | ✅ 200 | 204 个链接 |
| OFweek | 新能源汽车 | https://nev.ofweek.com/ | 页面 | ✅ 200 | 265 个链接 |
| OFweek | 光伏 | https://solar.ofweek.com/ | 页面 | ✅ 200 | 327 个链接 |
| OFweek | 机器人 | https://robot.ofweek.com/ | 页面 | ✅ 200 | 654 个链接；OFweek 各频道服务端渲染，是行业子系统的主力源 |
| 集微网 | 首页（资讯列表） | https://www.laoyaoba.com/ | 页面 | ✅ 200 | 148 个链接，文章为 `/n/数字` 静态页；无独立 newslist 页（已证实 404/500），抓取即抓首页；robots 禁 `/api` |
| 财联社 | 电报 | https://www.cls.cn/telegraph | 页面 | ⚠️ 200 | Next.js SPA，页面本身只有 52 个链接，正文数据需调 `https://www.cls.cn/v1/roll/get_roll_list`（本次返回“签名错误”，需实现其签名算法）或浏览器渲染 |
| 科创板日报 | 首页（官方站） | https://www.chinastarmarket.cn/ | 页面 | ✅ 200 | 178 个链接，文章为 `/detail/数字` 静态页；另有 `/telegraph`、`/subject` 频道，是财联社系里最容易抓的入口 |
| 北极星 | 电力新闻 | https://news.bjx.com.cn/ | 页面 | ⚠️ 200 | 间歇性 WAF JS 挑战（本次为验证页）；需重试 + Cookie 或浏览器兜底 |
| 北极星 | 光伏 | https://guangfu.bjx.com.cn/ | 页面 | ⚠️ 200 | 同上，本次为 WAF 验证页 |
| 北极星 | 储能 | https://chuneng.bjx.com.cn/ | 页面 | ✅ 200 | 397 个链接，本次正常返回真实内容；同一 WAF 下仍建议配重试 |
| 36氪 | RSS | https://36kr.com/feed | RSS | ✅ 200 | 30 条，最新 2026-08-02；feedparser 正常，推荐直接接入 |

### 4. 行业协会

| 站点 | 栏目 / 源 | URL | 类型 | 结果 | 说明 |
|---|---|---|---|---|---|
| CSIA 中国半导体行业协会 | 行业研究 | https://web.csia.net.cn/hyyjbg | 页面 | ⚠️ 200 | 万网云建站，HTML 内无链接（0 个），正文由 CDN 静态 JS（`img.wanwang.xin/.../*.Body.js`，约 300KB）渲染；可解析 Body.js 提取或浏览器渲染；旧域 `csia.net.cn` 已跳转到新域 |
| CPIA 中国光伏行业协会 | 官网首页 | https://www.chinapv.org.cn/ | 页面 | ✅ 200 | 303 个链接，含协会新闻、行业资讯、政策月报入口；注意 `cpia.org.cn` 非协会官网且不可访问；`/Association/list8.html` 等栏目页 200 但内容 JS/空，以首页为准 |
| 中汽协 | 行业信息 | http://www.caam.org.cn/chn/4/cate_30/ | 页面 | ✅ 200 | 367 个链接，静态列表 |
| 中汽协 | 产销数据 | http://www.caam.org.cn/chn/4/cate_31/ | 页面 | ✅ 200 | 367 个链接，月度产销数据 |

### 5. 国际源

| 站点 | 栏目 / 源 | URL | 类型 | 结果 | 说明 |
|---|---|---|---|---|---|
| Supply Chain Dive | RSS | https://www.supplychaindive.com/feeds/news/ | RSS | ✅ 200 | 10 条，最新 2026-07-31 |
| Supply Chain Dive | 物流频道 | https://www.supplychaindive.com/topic/logistics/ | 页面 | ✅ 200 | 177 个链接 |
| Supply Chain Dive | 采购频道 | https://www.supplychaindive.com/topic/procurement/ | 页面 | ✅ 200 | 172 个链接 |
| Manufacturing Dive | RSS | https://www.manufacturingdive.com/feeds/news/ | RSS | ✅ 200 | 10 条，最新 2026-07-31；Industry Dive 系列可直接复用 |
| IndustryWeek | RSS | `https://www.industryweek.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22home%22%7D` | RSS | ✅ 200 | 25 条，最新 2026-07-29；Brightspot 接口，`/rss` 路径是 404 |
| SemiEngineering | RSS | https://semiengineering.com/feed/ | RSS | ✅ 200 | 10 条，最新 2026-07-31，半导体深度分析 |
| C&EN | 商业栏目 | https://cen.acs.org/topics/business.html | 页面 | ✅ 200 | 136 个链接；C&EN 已停 RSS（/rss、/feed、Feedburner 均 404） |
| C&EN | 能源栏目 | https://cen.acs.org/topics/energy.html | 页面 | ✅ 200 | 128 个链接，化工/材料/能源产业新闻 |
| 集邦咨询 TrendForce | 官网 | https://www.trendforce.cn/ | 页面 | ✅ 200 | 195 个链接，半导体/LED/面板/新能源产业研究（补充源） |

## 三、抓取接入建议（面向 IIS collector）

### 3.1 RSS 优先接入（成本最低）

可直接用现有 `RssFeedsBasedCrawlFlow`：36氪、Supply Chain Dive、Manufacturing Dive、IndustryWeek、SemiEngineering。C&EN 无 RSS，用栏目页 + `fetch_content`。

### 3.2 页面接入优先级（按稳定性排序）

1. 科创板日报 `chinastarmarket.cn`（静态详情页，最省事）
2. OFweek 各频道、集微网首页、中汽协、CPIA 首页、国家统计局、前瞻、艾瑞报告中心、TrendForce（服务端渲染）
3. 工信部政策文件库（先摸清检索接口，其次浏览器渲染）
4. 北极星（配置重试 + Cookie，储能频道已验证可通）

### 3.3 需要浏览器渲染或特殊处理的源

- 信通院（412 WAF）：先用 `gma.caict.ac.cn` 专题页，或 Playwright 渲染后抓；白皮书正文常以 PDF 附件形式存在。
- 财联社电报：实现 `get_roll_list` 签名参数（社区有公开算法），或直接用科创板日报页面替代。
- 亿欧 / GGII / CSIA：亿欧与 GGII 当前直连不可行，建议走微信公号与第三方转载；CSIA 可解析其 CDN `Body.js` 数据（该文件包含完整页面内容）。
- 北极星主站与光伏频道：WAF 间歇触发，抓取任务需带失败重试与浏览器兜底。

### 3.4 与 IIS 子系统的对应（建议，可按配置调整）

- `industry` 子系统：OFweek、集微网、北极星、CPIA、CSIA、中汽协、赛迪、前瞻、GGII、信通院、工信部、国家统计局（制造业部分）、Supply Chain Dive / Manufacturing Dive / IndustryWeek / SemiEngineering。
- `finance` 子系统：财联社电报、科创板日报、36氪（财经科技类目）、艾瑞、亿欧、TrendForce（部分）。
- 各站点任务的 `subsystem` 归属通过 `_config/collector.json` 的 `task_dirs` 目录映射配置，无需改代码；建议 `CrawlTasks/industry/` 与 `CrawlTasks/finance/` 分目录维护。

## 四、附录

- 机器可读验证结果：`_export/intel_source_verified.json`（43 条：URL、HTTP 状态、链接数、WAF 标记、RSS 条目数/最新日期）。
- 验证说明：本次全部为在线真实请求，日期 2026-08-02；WAF 类站点（北极星、信通院）行为随时间波动，接入后建议保留重试与告警。
- 未纳入本次范围但值得后续评估的 finance 源：东方财富/同花顺行业数据、财新产业频道、Wind/万得行业研报聚合页（多为付费或强反爬，需单独评估）。
