## IntelligenceIntegrationSystem

情报整合系统：通过抓取主流新闻网站的公开新闻，并使用AI进行分析和评分的情报系统。属于OSINT的一种。

项目地址：[https://github.com/SleepySoft/IntelligenceIntegrationSystem](https://github.com/SleepySoft/IntelligenceIntegrationSystem/tree/dev)


## v2版本的说明

从2026年2月15日开始，main分支将正式切换到v2版本。

同时v1版本归档到 [这个分支](https://github.com/SleepySoft/IntelligenceIntegrationSystem/tree/iis-v1)。

v2版本兼容v1版本的数据，数据库不需要额外的升级操作。
最新导出的数据在 [这里下载](https://pan.baidu.com/s/18PpJPCkIkB9EB2ea5zOA5Q?pwd=djts)

关于v2版本的设计理念，以及与v1版本的区别，请阅读 [这篇文章]((doc/iis_v2_concept.md)) 。

除了情报数据结构及评分机制改变外，v2版本还对爬虫框架进行了进一步的改进。
一方面通过界面“所见即所得”的方式生成爬虫配置，另一方面增加了监控及调度功能，今后接入网站会非常方便。

另外向量数据库存储的格式也进行了调整，即将部分内容直接置入metadata中。借助向量数据库，IIS系统支持相似情报跳转及关联情报推演功能。

经过调整后的向量数据库对关联情报的查询表现优异，接下来我会重点研究情报的聚合以及关联推演。


## 更新与通知

N/A


## 起因

现在的新闻素质堪忧，特别是国内媒体。

+ 一方面现代社会每日产生的新闻数量巨大，并且参杂着毫无情报价值的水文。

+ 另一方面媒体看重点击量，所以标题成为了吸引点击的工具而非事件的概括。

我一直认为，公开信息中有四成只需要看标题，有两成看总结即可，而只有不到一成的信息有阅读全文的必要。而剩下的三成都是垃圾。

既然媒体们自己不体面，那么我来让新闻体面。


## 已接入的媒体

```js
{ domain: "aa.com.tr", nameCN: "阿纳多卢通讯社", country: "Turkey", flag: "🇹🇷", accessibleInChina: true },
{ domain: "abc.net.au", nameCN: "澳大利亚广播公司", country: "Australia", flag: "🇦🇺", accessibleInChina: false },
{ domain: "aljazeera.com", nameCN: "半岛电视台", country: "Qatar", flag: "🇶🇦", accessibleInChina: true },
{ domain: "bbc.com", nameCN: "英国广播公司", country: "UK", flag: "🇬🇧", accessibleInChina: false },
{ domain: "cbc.ca", nameCN: "加拿大广播公司", country: "Canada", flag: "🇨🇦", accessibleInChina: false },
{ domain: "chinanews.com", nameCN: "中国新闻网", country: "China", flag: "🇨🇳", accessibleInChina: true },
{ domain: "dw.com", nameCN: "德国之声", country: "Germany", flag: "🇩🇪", accessibleInChina: false },
{ domain: "elpais.com", nameCN: "国家报", country: "Spain", flag: "🇪🇸", accessibleInChina: false },
{ domain: "investing.com", nameCN: "英为财情", country: "International", flag: "🌍", accessibleInChina: true },
{ domain: "news.cn", nameCN: "新华网", country: "China", flag: "🇨🇳", accessibleInChina: true },
{ domain: "nhk.or.jp", nameCN: "日本广播协会", country: "Japan", flag: "🇯🇵", accessibleInChina: true },
{ domain: "ntv.com.tr", nameCN: "土耳其主流媒体 NTV", country: "Turkey", flag: "🇹🇷", accessibleInChina: true },
{ domain: "rfi.fr", nameCN: "法国国际广播电台", country: "France", flag: "🇫🇷", accessibleInChina: false },
{ domain: "tass.com", nameCN: "塔斯社", country: "Russia", flag: "🇷🇺", accessibleInChina: true },
{ domain: "voanews.com", nameCN: "美国之音", country: "USA", flag: "🇺🇸", accessibleInChina: false },
```

## 环境配置及部署运行

#### 依赖软件

+ MongoDB

程序使用MongoDB数据库存储情报文档，这是一个NoSql数据库，请在官网下载：
> 
> https://www.mongodb.com/products/self-managed/community-edition
> 

同时建议安装mongodb tools，用以导出及导出数据库（从某个版本开始命令行工具不再和MongoDB主程序打包）：
> 
> https://www.mongodb.com/try/download/database-tools
> 

#### 向量模型（向量数据库用）

[bge-m3](https://huggingface.co/BAAI/bge-m3)


#### 创建与激活虚拟环境（可选）

本项目建议使用python版本为3.10以上。当然，不创建虚拟环境，直接使用系统默认的python环境也不是不行。

> 所谓python虚拟环境，其实非常简单，它就是一个目录，当你切换到这个虚拟环境时，使用的解析器、安装的库、使用的库，都仅限于该目录下，从而和其它环境隔离。
> 
> 知道这个原理后，大家应该能想到：使用pycharm时，选择已创建的虚拟环境其实就是选择这个目录下的python.exe。

创建虚拟环境主要有三种方法：
+ 原生的venv
+ Anaconda（及其兼容方法）
+ uv

##### venv

```
# 创建虚拟环境
# - 通常在项目目录下执行以下命令，虚拟环境的python版本跟你当前运行的python环境有关
# - 对我来说，通常是在anaconda下创建一个指定版本python的虚拟环境，再使用这个虚拟环境创建venv
# - （那为什么不直接用anaconda？好问题。）

python -m venv .venv

# 切换到该虚拟环境（接下来安装依赖前都需要先切换到该虚拟环境，下同）

# ---- Windows ----
.venv\Scripts\activate.bat

# ----- Linux -----
source .venv/Scripts/activate
```

##### Anaconda

Anaconda好处是方便，缺点是重

下载：https://www.anaconda.com/download

```
# 创建虚拟环境
conda create -n iis python=3.10

# 切换到该虚拟环境
conda activate iis
```

##### uv

这是现在流行的工具，非常轻量，而且快，不过我还不是很熟悉。以下内容来自AI。

```
# 安装uv
pip install uv

# 创建虚拟环境
uv venv

# 切换到该虚拟环境
.venv\Scripts\activate.bat

# 接下来可以使用pip install -r，也可以使用uv的方式安装依赖（自行查阅）。
```

#### 程序部署与安装依赖

```
# Clone this project to your local
git clone https://github.com/SleepySoft/IntelligenceIntegrationSystem.git

# Enter this project dir
cd IntelligenceIntegrationSystem

# Important: Fetch sub modules
git submodule update --init --recursive

# --------------------------------------------------------------------------------- #
# ! Reference to above: Create virtual environment and switch to this environment ! #
#           Use `conda activate iis` or `.venv\Scripts\activate.bat`                #
# --------------------------------------------------------------------------------- #

# Old pip version will not support utf-8, so upgrade to newest pip first.
python.exe -m pip install --upgrade pip

# Install dependency
pip install -r requirements.txt
pip install -r IntelligenceCrawler/requirements.txt
pip install -r PyLoggingBackend/requirements.txt
pip install -r AIClientCenter/requirements.txt
pip install -r VectorDB/requirements.txt

# Optional: If has dependency issue when using upper command, use this command instead: 
pip install -r requirements_freeze.txt

# After pip install. Install playwright's headless browser
playwright install chromium

# ------------------------------------------------------------------------------------
# ! Before launching program, you should do some config first (read following section)
# ------------------------------------------------------------------------------------

# Run the Vector DB (Optional)
python VectorDB/VectorDBBService.py \
    --db-path D:\Code\IntelligenceIntegrationSystem\_data\VectorDB 
    --model D:\Code\bge-m3

# Run main service
python IntelligenceHubLauncher.py

# Run collectors
python CrawlerServiceEngine.py
```

#### 程序配置

+ 重要：将 [config_example.json](_config/config_example.json) 复制为 [config.json](_config/config.json) ，按照实际情况更改配置（默认能启动，但不能进行分析）。
> 
> 配置主要应用在在 [IntelligenceHubStartup.py](IntelligenceHubStartup.py) 中载入，阅读该文件可以知道各配置项的用法。
> 
> 对于抓取外网新闻，需要配置 global_site_proxy。
> 
> 注意intelligence_hub_web_service及collector段的token并非AI服务的Token，而是爬虫引擎提交情报时的凭证以及IHub接受提交情报的凭证，两边需要对上（因为二者可以部署在不同的机器上）。
> 

+ 重要：运行 [UserManagerConsole.py](Scripts/UserManagerConsole.py) ，按提示增加一个用户并设置密码，否则后台无法登录。

+ AI服务配置
> 
> 如果想使用AI进行情报分析，需要配置 [ai_client_config.py](_config/ai_client_config.py) 。由于AI服务配置比较复杂，因此直接使用python文件而非json文件。
> 
> 配置请参考：[AIClientConfigExample.py](AIClientCenter/AIClientConfigExample.py) ，按需要复制修改对应项即可。
> 

#### 启动

完整的功能需要启动3个Python程序：

+ [IntelligenceHubLauncher.py](IntelligenceHubLauncher.py)
> 
> 核心服务，启动该服务后即可通过网页访问其主要功能。
> 

+ [CrawlerServiceEngine.py](CrawlerServiceEngine.py)
> 
> 爬虫服务，启动该服务才能抓取情报/新闻。
> 

+ [可选] [VectorDB/VectorDBBService.py)](VectorDB/VectorDBBService.py)
> 
> 向量数据库服务，开启该服务后可以使用高级的搜索及关联功能。
> 
> 参考启动命令：
>   ```commandline
>   python VectorDB/VectorDBBService.py \
>     --db-path C:\Code\git\IntelligenceIntegrationSystem\_data\VectorDB \
>     --model C:\Code\git\bge-m3 \
>     --agg-store-dir C:\Code\git\IntelligenceIntegrationSystem\_data\Aggressive
>   ```
> 
> 如果没有下载离线向量数据库，可以通过 [rebuild_vector_index.py](Scripts/rebuild_vector_index.py) 脚本增量新建向量索引。参考启动命令：
>   ```commandline
>       python Scripts/rebuild_vector_index.py rebuild
>   ```
> 

#### 使用

+ 打开 [localhost:5000/login](localhost:5000/login) 输入刚才配置的账号密码进入后台页面。

+ 打开 [localhost:8001](localhost:8001) 进入向量数据库的管理页面。

+ 打开 [localhost:5000](localhost:5000) 则是无密码的公开页面。


## 其它工具

+ MongoDB工具
  > https://www.mongodb.com/try/download/database-tools
  > 
  > 用以导出/导出MongoDB记录，可以配合[mongodb_exporter.py](Scripts/mongodb_exporter.py)一系列脚本使用。


## 程序架构

### 原理

本程序核流程为：抓取 -> 提交到情报中心 -> 清洗、AI分析 -> 筛选并重发布 -> 归档

### 模块

#### 抓取

> 本程序通过RSS抓取公开新闻，原因在于这类新闻抓取难度小（本身就是给RSS阅读器的公开信息），且法律风险低。对于没有RSS的网站，也支持列表页抓取。

程序中由[CrawlerServiceEngine.py](CrawlerServiceEngine.py)驱动[CrawlTasks](CrawlTasks)目录下的抓取模块。
> 
> 该服务框架会监控该目录下的文件更新并重新加载更新后的模块。
> 

抓取模块事实上基于两个框架：

+ 之前的简易框架：[RssFeedsBasedCrawlFlow.py](Workflow/RssFeedsBasedCrawlFlow.py)
> 
> 抓取模块通过partial构建偏函数供抓取流程调用。
>

+ 新的AIClientCenter框架：[AIClientCenter](AIClientCenter)
> 
> 通过[CrawlerPlayground.py](IntelligenceCrawler/CrawlerPlayground.py)可视化生成抓取代码，
> 通过[IntelligenceCrawlFlow.py](Workflow/IntelligenceCrawlFlow.py)适配IIS抓取与提交流程。
> 

无论是哪个框架，最终都通过 [CommonFlowUtility.py](Workflow/CommonFlowUtility.py) 将抓取到的内容提交到IntelligenceHub。
> 
> 实际上采集数据只需要按照 ```class CollectedData``` 定义的格式将数据通过 POST 提交到IHub的 ```/collect``` 端点即可，无论采用什么方式抓取。
> 唯一需要注意的是如果设置了安全Token，则提交数据时要将该凭据附上。至于数据的来源，IHub并不关心，抓取服务和IHub也不一定要运行在同一台电脑上。
> 
> 使用通用流程的好处在于增加一个抓取任务非常方便，并且通用流程中实现了抓取记录和防重复抓取的功能。
> 
> 如果需要抓取的网页需要特殊技术，则需要自己实现抓取器。同时注意法律风险。
>

### IntelligenceHub

+ [IntelligenceHub.py](IntelligenceHub.py)（IHub）：程序的核心。所有的信息都会提交汇总至此，由该模块进行处理、分析、归档，并提供查询功能。
+ [IntelligenceHubWebService.py](IntelligenceHubWebService.py)：为IHub提供网络服务的模块，包括API、网页发布和鉴权。
+ 
+ [IntelligenceHubStartup.py](IntelligenceHubStartup.py)：初始化所有子组件、IntelligenceHub和IntelligenceHubWebService。
+ [IntelligenceHubLauncher.py](IntelligenceHubStartup.py)：IHub的**启动**器，选用合适的backend载入IntelligenceHubWebService的wsgi_app，
  > [20250910] 提供Flask原生、waitress、gunicorn三种WSGI服务器，默认服务器为waitress。
  > 
  > 注意gunicorn仅支持Linux。
  > 
  > 该文件不包含业务代码，几乎全部由AI生成，没有阅读的必要。如果对启动原理不理解，可以去搜索WSGI的机制。
  > 

> IHub的处理流程请参见：[IIS_Diagram.drawio](doc/IIS_Diagram.drawio)

### 分析

+ [prompts_v2x.py](prompts_v2x.py)

+ [ServiceComponent/IntelligenceHubDefines_v2.py](ServiceComponent/IntelligenceHubDefines_v2.py)

  情报分析的prompt以及格式定义。程序中的dict校验和该prompt指示的输出格式紧密相关，prompt对AI的要求必须遵守校验规则。
  
  已知的问题为：
  
  1. 该prompt在小模型（甚至于65b）上表现不佳。
  > 
  > 在小模型上AI通常不按规定格式输出，有可能是prompt + 文章内容太长，使AI无法集中注意力的缘故。
  > 
  > 正式部署的环境使用的是满血云服务，这是一笔不小的开支。
  > 
  
  2. AI评分还是过于宽松，没有达到我的期望。
  >
  > 
  > 对于情报的评分偏高，我理想中80%的新闻应当处于6分及以下的区间。
  > 
  
  3. AI偶尔会不按要求输出非中文内容。
  >
  > 
  > 通过增加翻译模块解决。
  > 

+ [IntelligenceAnalyzerProxy.py](ServiceComponent/IntelligenceAnalyzerProxy.py)

  > AI分析实现的主要文件。调用AI Client，组织数据、使用prompt进行分析，并解析和返回结果。
  > 
  > 值得一提的是，自从使用了json_repair后，python的解析率几乎100%。接下来我会尝试在小模型上使用constrained decoding，看是否能提升表现。
  >

### 内容发布

如前所述，网络服务由[IntelligenceHubWebService.py](IntelligenceHubWebService.py)提供。包含以下内容：

+ 登录与鉴权
    > 由 WebServiceAccessManager 和 [UserManager.py](ServiceComponent/UserManager.py) 进行管理。其中：
    >  
    > + API Token位于配置文件中：[config_example.json](config_example.json)
    > + 登录与注销的页面分别为：'/login'，'/logout'
    > + 用户信息保存在Authentication.db，通过[UserManagerConsole.py](Scripts/UserManagerConsole.py)管理用户。

+ WebAPI
    > '/api'接口：采用 [ArbitraryRPC.py](MyPythonUtility/ArbitraryRPC.py) ，不用额外编码或配置即可调用Stub的所有函数，同时支持任意层次的转发调用。
    > 
    > '/collect'接口：收集采集情报。
    > 
    > 其它API接口：子功能，如Log、统计、监控等等，由对应模块注册路由。
    >

+ 网页
    > 不使用前后端的架构，所有内容由服务器生成。包括以下文件：
    > 
    > [PostManager.py](ServiceComponent/PostManager.py)：根据 [posts](posts) 目录下的markdown文件生成HTML。
    > > [posts/index.md](posts/index.md)：公共主页
    > > 
    > > [posts/index_public.md](posts/index_public.md)：后台管理主页
    > 
    > [intelligence_detail.html](templates/intelligence_detail.html)：文章详情页。
    > 
    > [intelligence_list.html](templates/intelligence_list.html)：文章列表页。
    > 
    > [intelligence_cluster_list.html](templates/intelligence_cluster_list.html)：文章聚合页。
    > 
    > [intelligence_search.html](templates/intelligence_search.html)：文章查询页。
    > 
    > [intelligence_graph.html](templates/intelligence_graph.html)：情报态势追踪页。
    > 
    > 子功能页面，由对应模块提供，这里就不再一一列出，详见登录后的管理页面。
    > 

### 存储与目录结构

#### 目录结构

为了方便docker等部署需要，本项目将程序和数据分离。数据按其属性置于以下目录中：

+ [_config](_config)
> 配置文件，由用户手动编辑。

+ [_data](_data)
> 程序产生或暂存的数据，不要手动编辑。是最重要的目录，也是重点需要备份的目录。包含以下内容：
> + 向量数据库文件
> + 聚类记录
> + 爬虫抓取记录
> + AI客户端管理相关文件

+ [_export](_export)
> 程序导出的数据，比如数据库文件。

+ [_log](_log)
> 程序的log文件，历史log会自动归档和管理。

+ [_products](_products)
> 程序的产物目录，预留。

#### 数据库存储

+ 情报存储（主要）
  > MongoDB，默认数据库名：IntelligenceIntegrationSystem。包含以下记录：
  > + intelligence_cached：Collector提交的采集到的原始新闻数据。
  > + intelligence_archived：经过处理并归档的数据。
  > + intelligence_low_value：被抛弃的低价值信息，用以作为模型训练的负例。
  > + intelligence_storylines：情报态势推演记录。


## 意见和建议

如果有意见和建议，可以到这个讨论下留言：[https://zhuanlan.zhihu.com/p/1957783829684154704](https://zhuanlan.zhihu.com/p/1957783829684154704)

或者可以加入wx或QQ讨论组（验证消息：IIS）：

![wx.png](doc/wx_group_qr.png)

![qq.png](doc/qq_group_qr.png)
