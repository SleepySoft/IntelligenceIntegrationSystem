# IIS systemd 服务配置

本目录提供 IIS 三个核心服务的独立 systemd 单元文件，替代旧的单一 `iis.service`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `iis.env` | 三个服务共享的环境变量 |
| `iis-vectordb.service` | 向量数据库服务（VectorDBBService） |
| `iis-web.service` | Web 核心服务（IntelligenceHubLauncher） |
| `iis-crawler.service` | 爬虫引擎服务（CrawlerServiceEngine） |
| `iis.target` | 一键拉起全部三个服务 |

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
sudo systemctl status iis-vectordb iis-web iis-crawler

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

可通过 `_config/config.json` 关闭：

```json
"intelligence_hub": {
  "systemd_watchdog": {
    "enabled": false,
    "interval_sec": 10
  }
}
```

在 Windows 或非 systemd 环境下该功能自动变为 no-op。

## 迁移说明

旧的 `iis.service` 与 `launch_iis_system.sh` 进程组管理模式已被替换。
systemd 现在直接管理每个进程，`launch_iis_system.sh` 仅保留供手工调试使用。
