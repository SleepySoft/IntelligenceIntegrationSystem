# IIS Linux Deployment

本目录承载 IIS 的 Linux 单命令部署工具与部署文档，不放置业务代码。

当前已完成 **M0：可落地性审计与只读诊断**，正在实施 **M1：运行时路径与配置单一事实
源**。安装器尚未开放，避免把现有机器的硬编码路径包装成一个不可靠的“一键脚本”。
完整问题清单、改造顺序和验收标准见
[`AUDIT_AND_ROADMAP.md`](AUDIT_AND_ROADMAP.md)。

M1 已支持按需外置以下目录，同时保持仓库内运行的默认行为不变：

```text
IIS_CONFIG_DIR
IIS_DATA_DIR
IIS_LOG_DIR
IIS_EXPORT_DIR
IIS_PRODUCTS_DIR
IIS_MODEL_DIR
IIS_CONFIG          # 显式主配置文件；设置后不存在则直接失败，不回退示例
```

## 当前可用命令

从仓库根目录执行：

```bash
deploy/iisctl doctor
deploy/iisctl doctor --json
deploy/iisctl configure
```

`doctor` 只读取主机、仓库、配置文件权限和现有 systemd 模板，不安装软件、不修改配置、
不启动或停止服务，也不会输出 Token、密码或 API Key。退出码含义：

- `0`：没有硬阻塞；仍可能有需要处理的 warning。
- `1`：存在当前主机或仓库级硬阻塞。
- `2`：命令参数或诊断程序自身错误。

`configure` 启动只监听 `127.0.0.1:5070` 的配置向导，不启动 MongoDB、AI Client 或 IIS
业务服务。正式 `_config/iis.yaml` 不存在时，引导页会从示例加载默认值，依次配置 Web、
MongoDB、访问令牌和可选功能；保存后再前往 AIClientCenter 自己的配置页面。也可指定路径：

```bash
deploy/iisctl configure --config /etc/iis/iis.yaml --port 5070
```

## 目标命令（后续里程碑）

```bash
sudo deploy/iisctl install --profile core
deploy/iisctl status
deploy/iisctl logs web
deploy/iisctl doctor --runtime
sudo deploy/iisctl upgrade
sudo deploy/iisctl rollback
sudo deploy/iisctl backup
```

首版正式支持目标为 Ubuntu 22.04/24.04、Debian 12、x86_64。基础 `core` profile 只安装
IIS Web、Crawler、Python 环境和爬虫所需 Chromium，默认关闭 VectorDB。MongoDB 以及
Codex/Kimi/Ollama 等 AI 程序均视为外部能力：安装器只检查连接或可执行性，不负责安装、
登录、升级或备份。首版服务直接以发起安装的普通用户运行。
