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

每个服务在启动前都会运行 `Scripts/deployment_preflight.py`。检查失败时服务不会进入
重启循环；先按报错补齐配置、模型或依赖，再重启对应服务。也可以手工运行：

```bash
/home/sleepy/anaconda3/envs/iis/bin/python Scripts/deployment_preflight.py --component all
```

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
