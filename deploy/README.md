# IIS Linux Deployment

本目录承载 IIS 的 Linux 单命令部署工具与部署文档，不放置业务代码。

当前已完成只读诊断、运行时路径单一事实源、配置向导和无人值守安装初版。
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
IIS_RUNTIME_ROOT     # 六个下划线目录的共同父目录；单目录变量优先
IIS_CONFIG          # 显式主配置文件；设置后不存在则直接失败，不回退示例
```

## 当前可用命令

从仓库根目录执行：

```bash
deploy/iisctl doctor
deploy/iisctl doctor --json
deploy/iisctl paths
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
deploy/iisctl configure --config /opt/iis/_config/iis.yaml --port 5070
```

配置网页的职责到此结束：它不会安装依赖、调用 sudo、操作 systemd 或启动业务进程。完成
IIS 与 AI Client 配置后，使用无人值守安装器：

```bash
sudo deploy/iisctl install --non-interactive \
  --config ./_config/iis.yaml \
  --ai-config ./_config/ai_clients.yaml \
  --admin-user admin \
  --admin-password-file /run/secrets/iis-admin-password
```

密码只从权限受控文件读取，不进入命令参数值、配置 YAML 或安装日志。Crawler 配置未显式
提供时，安装器会从 IIS 的监听端口、默认子系统和第一个 collector token 生成安全骨架。
安装器默认以 `/opt/iis` 为根创建 `_config`、`_data`、`_log`、`_export`、`_products`、
`_models` 和虚拟环境，并生成无本机硬编码的 systemd 单元；只有 systemd 单元本身进入
`/etc/systemd/system`。已有不同配置默认拒绝覆盖，`--force-config` 会先备份到
`_data/deployment/config-backups`。

当前安装初版仍以 `--source` 指定的源码目录作为程序目录，只迁移运行环境和动态目录；
源码 Release、`releases/<version>` 与 `current` 原子切换属于后续发布产物阶段，本轮不改变
仓库内直接运行的默认行为。

### 沙箱安装与分层测试

同一安装后端可把所有系统路径投影到临时目录，不接触宿主 systemd：

```bash
deploy/iisctl install --non-interactive \
  --config ./_config/iis.yaml \
  --ai-config ./_config/ai_clients.yaml \
  --install-root /opt/iis \
  --sandbox-root /tmp/iis-install-test \
  --service-user iis-test \
  --skip-dependencies --skip-browser --no-systemd --no-start --skip-admin
```

测试刻意分开：

```bash
# 配置页面后端、schema/UI 契约和保存行为
PYTHONPATH=. pytest -q Test/test_configuration_pages.py

# 无人值守安装器、幂等性、权限和网页配置交接契约
pytest -q deploy/tests --ignore=deploy/tests/web

# 真实浏览器只验证网页交互，不执行安装
pip install -r deploy/requirements-test.txt
playwright install chromium
PYTHONPATH=. pytest -q deploy/tests/web
```

`IISSettings` 和 AI Client 公共配置契约都保存了已审查的 UI schema 指纹。schema 变化而
页面未重新确认时，页面会显示警告，对应自动化测试也会失败。

运行时路径解析遵循：单目录环境变量 > `IIS_RUNTIME_ROOT` 下的同名下划线目录 > 项目根
下的默认目录。不设置新变量时保持仓库内运行的原行为。用 `deploy/iisctl paths` 可查看
每个最终路径、选择来源和配置文件是否存在。

## 后续命令

```bash
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
