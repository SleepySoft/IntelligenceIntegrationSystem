# IIS systemd 服务配置

本目录提供 IIS 三个核心服务的独立 systemd 单元文件，替代旧的单一 `iis.service`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `iis.env` | 三个服务共享的环境变量 |
| `iis-vectordb.service` | 向量数据库服务（VectorDBBService） |
| `iis-web.service` | Web 核心服务（IntelligenceHubLauncher） |
| `iis-crawler.service` | 爬虫引擎服务（CrawlerServiceEngine） |
| `iis.target` | 拉起当前启用的 Web 与爬虫服务（VectorDB 默认关闭） |
| `iis-dashboard.service` | 开机创建 IIS 日志与系统监控 tmux 仪表盘 |

付费 AI API Key 不写入 `iis.env` 或 YAML。Web 服务会额外读取部署机本地的
`/home/sleepy/.config/iis/ai-clients.env`（权限应为 `0600`）；声明式 Client 配置仅通过
`api_key_env` 引用变量名。该文件不属于仓库，也不会被提交。

每个服务在启动前都会运行 `Scripts/deployment_preflight.py`。检查失败时服务不会进入
重启循环；先按报错补齐配置、模型或依赖，再重启对应服务。也可以手工运行：

```bash
/home/sleepy/anaconda3/envs/iis/bin/python Scripts/deployment_preflight.py --component all
```

## 容量要求

关闭 VectorDB 和翻译子服务、使用单一 headless 代理时，最低建议为 2 vCPU、4 GiB
内存、2 GiB Swap 和 30 GiB 可用 SSD；推荐为 4 vCPU、8 GiB 内存、4–8 GiB Swap 和
100 GiB NVMe/SSD。GPU 不是必需项。

该口径包含 IIS Web、Crawler、MongoDB、Nginx、Playwright 临时子进程和代理 core，
不包含桌面 Clash Verge GUI。MongoDB 内存会随工作集增长，并非固定占用 4 GiB。
实测数据、峰值口径和磁盘留存要求见
[部署资源要求](../doc/deployment_resource_requirements.md)。

## 安装步骤

```bash
sudo ln -s /home/sleepy/Documents/IntelligenceIntegrationSystem/systemd/iis-vectordb.service /etc/systemd/system/
sudo ln -s /home/sleepy/Documents/IntelligenceIntegrationSystem/systemd/iis-web.service      /etc/systemd/system/
sudo ln -s /home/sleepy/Documents/IntelligenceIntegrationSystem/systemd/iis-crawler.service  /etc/systemd/system/
sudo ln -s /home/sleepy/Documents/IntelligenceIntegrationSystem/systemd/iis.target           /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable iis.target
sudo systemctl start iis.target
```

## 常用命令

```bash
# 查看全部
sudo systemctl status iis-web iis-crawler

# 单独重启某个服务
sudo systemctl restart iis-web

# 日志
sudo journalctl -u iis-web -f

# 查看 watchdog 是否正常工作（应能看到 WATCHDOG=1 通知）
sudo systemctl show iis-web --property=WatchdogTimestamp
```

## systemd watchdog

`iis-web.service` 已启用 `WatchdogSec=30`。IIS 主进程启动后会启动一个守护线程，
每 10 秒向 systemd 发送 `WATCHDOG=1`。若主线程或 GIL 被长时间占住导致喂狗线程无法运行，
systemd 将在 30 秒后自动重启 `iis-web`。

watchdog 当前由主配置的运行时默认值启用；在 Windows 或非 systemd 环境下自动变为
no-op。systemd 单元的 `WatchdogSec=30` 必须大于程序的 10 秒通知间隔。

## 迁移说明

旧的 `iis.service` 与 `launch_iis_system.sh` 进程组管理模式已被替换。
systemd 现在直接管理每个进程，`launch_iis_system.sh` 仅保留供手工调试使用。

旧 `_config/config.json` 不再被生产入口读取。升级部署必须将其拆分为
`_config/iis.yaml`、`_config/ai_clients.yaml` 和 `_config/collector.json`；三个真实配置
均被 git 忽略。不要依赖示例配置回退，preflight 会将缺失的生产配置视为错误。

现有部署的向量聚合历史位于项目根目录 `aggregation_store/`，服务单元显式沿用该路径。
若迁移到 `_data/`，应先停 `iis-vectordb`，完整移动数据，再同步修改
`--agg-store-dir`，避免服务以空目录启动。

当前部署在 `_config/iis.yaml` 中关闭向量子系统，`iis.target` 因此不拉起
`iis-vectordb.service`。若以后重新启用，需要同时恢复 target/web 对 VectorDB 单元的
`Wants`/`After` 关系，或单独启用并启动 `iis-vectordb.service`。

## 运维仪表盘

一键安装并启用后台仪表盘及 GNOME 登录自启动：

```bash
Scripts/install_iis_dashboard.sh
```

仪表盘默认打开一屏四区域的监控墙，同时显示总览、Web 日志、Crawler 日志和 VectorDB
日志；此外保留各区域的独立大窗口和 `btop`。总览显示服务状态、API 健康、
CPU/GPU/NVMe 温度、负载、内存、磁盘、网络地址和高占用进程。VectorDB 未安装时，
其日志窗口会显示等待提示，不会回放残留的历史日志，也不会导致仪表盘退出。

```bash
# 图形终端中一键打开
Scripts/iis_dashboard.sh open

# 当前终端中进入
Scripts/iis_dashboard.sh attach

# 查看后台会话状态
Scripts/iis_dashboard.sh status
```

`F1` 返回监控墙；`F2` 到 `F6` 分别打开总览、Web、Crawler、VectorDB 和 `btop` 的
独立大窗口。也可以使用 `Alt+1` 到 `Alt+6` 或鼠标点击底部标签。监控墙中可直接用
鼠标拖动蓝色边框调整区域；`F7` 恢复四宫格，`F8` 切换主区域在左，`F9` 切换主区域
在上，`Alt+z` 临时放大/恢复当前区域。tmux 原生的 `Ctrl-b Space` 也可循环布局，
`Ctrl-b z` 可放大区域，`Ctrl-b d` 退出视图但保持后台运行。GNOME 登录后会自动打开
仪表盘，应用菜单中也可搜索 “IIS Operations Dashboard”。
