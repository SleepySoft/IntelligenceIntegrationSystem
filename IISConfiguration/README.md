# IIS 配置中心

该目录是独立的 IIS 主配置组件，只负责 `_config/iis.yaml`，不初始化 MongoDB、
AI Client、爬虫或其他业务设施。AI Client 配置仍由 `AIClientCenter` 自己拥有。

## 两种运行方式

主服务无法启动或首次安装时，单独运行：

```bash
python -m IISConfiguration
```

默认仅监听 `127.0.0.1:5070`。如需指定文件：

```bash
python -m IISConfiguration \
  --config /etc/iis/iis.yaml \
  --fallback ./_config/iis_example.yaml
```

IIS 正常运行后，配置页自动以 Blueprint 挂载到 `/admin/config/`，复用 IIS 后台
登录保护。独立模式故意禁止监听非本机地址；需要远程配置时应使用带认证的嵌入页或
SSH 端口转发。

## 保存语义

- 引导表单展示 `IISSettings` 的完整默认值；高级模式可直接编辑 YAML。
- 校验复用 `IISConfig.py` 的 Pydantic schema 和运行时投影检查。
- 校验失败不会修改正式文件。
- 保存使用“同目录临时文件 + fsync + `os.replace`”原子替换。
- 旧版本保存在配置目录下 `.config-history/iis/`，默认保留 30 份。
- 密码与 Token 返回浏览器前会替换为保留标记；未改动标记时保存会恢复旧值。
- IIS 配置保存后需要重启服务生效，页面不会擅自重启生产进程。

## 模块边界

`service.py` 不依赖 Flask，可由安装器、CLI 或测试直接复用。`web.py` 只包装 HTTP 与
CSRF，并可挂到任意 Flask 应用。`__main__.py` 是不依赖 IIS 业务启动的独立外壳。

AI Client 的对应页面由 `AIClientCenter.configuration` 和
`AIClientCenter.web.configuration` 提供：独立启动 `python -m AIClientCenter` 后访问
`/config/`；嵌入 IIS 后访问 `/monitor/ai-client-dashboard/config/`。其保存后可调用
AIClientCenter 原有的原子热重载，无需重启 IIS。

## 后续迭代

当前版本先覆盖配置闭环：读取、引导编辑、高级编辑、真实校验、密钥遮蔽、原子保存、
历史恢复和运行态应用。后续可在不改变后端契约的前提下增加安装向导、连通性检查、
字段级帮助与 systemd 重启授权；连通性检查应始终与“语法/结构校验”分开，避免保存
配置时产生第三方 API 费用。
