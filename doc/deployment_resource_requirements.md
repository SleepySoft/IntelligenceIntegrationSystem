# IIS 部署资源要求（不含 VectorDB）

本文给出 IIS Web、Crawler 及其本机依赖的容量基线。以下配置默认：

- 关闭 VectorDB 和翻译子服务；
- 使用外部 AI API，Codex CLI 仅按额度策略备用；
- Linux 无桌面部署，只运行一个 headless 代理 core；
- 同时运行 IIS Web、Crawler、MongoDB 和 Nginx；
- Crawler 使用当前受控并发，并在每轮任务结束后释放 Playwright 浏览器。

## 最低与推荐配置

| 资源 | 最低配置 | 推荐配置 |
|---|---:|---:|
| CPU | 2 vCPU | 4 vCPU |
| 物理内存 | 4 GiB | 8 GiB |
| Swap | 2 GiB | 4–8 GiB |
| 可用磁盘 | 30 GiB SSD | 100 GiB NVMe/SSD |
| GPU | 不需要 | 不需要 |
| 网络 | 稳定的 HTTPS 出站；受限网络需可用代理 | 低延迟出站及独立 headless 代理 |

最低配置适合当前抓取并发和中小规模数据保留；不建议在 2 GiB 主机上部署完整栈。
推荐配置为 MongoDB 工作集增长、Playwright 短时峰值、AI 并行请求以及日志维护保留了
余量。若长期保留全部爬虫历史、conversation 和调试日志，应按增长速度扩展磁盘，
而不是把 100 GiB 视为永久上限。

## 当前实测基线

2026-10-19 在 16 逻辑 CPU 的当前部署机上连续采样 60 秒；期间 Web 正在进行 AI 分析，
Crawler 实际启动并释放过一组 Playwright/Chromium 进程。systemd cgroup 统计包含子进程和
页缓存，CPU 的 100% 表示占满一个逻辑核心。

| 组件 | 平均内存 | 观测峰值 | 平均 CPU | 单秒 CPU 峰值 |
|---|---:|---:|---:|---:|
| IIS Web（含临时 Codex app-server） | 112 MiB | 202 MiB | 3.45% | 45.4% |
| Crawler（含临时 Chromium） | 368 MiB | 520 MiB | 4.87% | 81.6% |
| MongoDB | 474 MiB | 475 MiB | 1.62% | 6.4% |
| headless 代理服务 | 31 MiB | 31 MiB | 0.09% | 0.5% |
| Nginx | 25 MiB | 25 MiB | 0.01% | 0.1% |
| 合计 | 约 1.0 GiB | 保守峰值约 1.25 GiB | 约 10% 单核 | — |

平均 CPU 约为 0.10 个核心，在该 16 线程机器上约占整机容量的 0.63%。一次抓取任务中
出现 7 个 Chromium 子进程，Crawler 内存短暂上升到约 520 MiB；任务结束后子进程全部
退出，空闲 CPU 回落到约单核的 0.1%，未观察到浏览器泄漏。

以上是容量基线，不是硬性内存上限。站点数量、抓取并发、文章长度、MongoDB 工作集及
同一时间的 AI 分析数都会改变峰值。

## MongoDB 为什么不是固定占用 4 GiB

“MongoDB 要占 4 GiB”更适合作为小型生产主机的容量建议，而不是固定常驻内存。MongoDB
会根据数据库工作集、WiredTiger 缓存、索引和操作系统文件页缓存按需增长；刚启动或数据量
较小时不会预先锁定 4 GiB。本次实测 MongoDB cgroup 约 474 MiB，MongoDB 数据目录约
22 MiB。

因此：

- 不能因为当前只占约 474 MiB，就把整机内存压缩到 2 GiB；
- 也不需要在 IIS 之外额外为 MongoDB 固定预留 4 GiB；
- 4 GiB 主机是本部署范围的最低整机配置，8 GiB 更适合持续增长的数据集；
- 数据集进入数十 GiB、查询并发提高或大量索引进入活跃工作集后，应重新测量并考虑限制
  WiredTiger cache，而不是沿用本文的 474 MiB 实测值。

## 代理 GUI 与 headless 部署

上述最低/推荐配置不包含桌面 Clash Verge GUI。当前开发机上 GUI、WebKit 和其启动的
mihomo 额外占用约 737 MiB，且 systemd 下还存在一个重复但未监听业务端口的 mihomo。
将 GUI 算入后，当前整套相关进程约为 1.7 GiB，保守峰值接近 2.0 GiB。

生产部署应确保代理端口由唯一的 systemd/headless core 持有，再关闭 GUI；不能在当前
端口仍由 GUI 进程持有时直接退出 Clash Verge，否则 IIS 的外网请求会失去代理。

## 磁盘与留存

当前机器排除 `_data/VectorDB`、代码仓库和共享 Python 环境后，运行数据约 13 GiB：

| 项目 | 当前占用 |
|---|---:|
| `_data`（不含 VectorDB） | 6.7 GiB |
| `conversation` | 2.7 GiB |
| MongoDB 日志 | 2.6 GiB |
| Playwright Chromium 程序 | 895 MiB |
| IIS 日志 | 92 MiB |
| MongoDB 数据目录 | 22 MiB |

30 GiB 最低磁盘要求以启用日志轮转和定期清理 conversation 为前提。当前单个 MongoDB
日志已达到 2.6 GiB，应配置轮转/保留上限；Crawler 治理数据库和 conversation 同样需要
留存策略。未设置保留策略时，任何固定磁盘规格最终都会耗尽。
