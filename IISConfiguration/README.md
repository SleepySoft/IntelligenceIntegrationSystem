# IIS 配置中心

该目录是独立的 IIS 主配置组件，只负责 `_config/iis.yaml`，不初始化 MongoDB、
AI Client、爬虫或其他业务设施。AI Client 配置仍由 `AIClientCenter` 自己拥有。

## 两种运行方式

主服务无法启动或首次安装时，单独运行：

```bash
python -m IISConfiguration
# 或使用统一部署入口
deploy/iisctl configure
```

默认仅监听 `127.0.0.1:5070`。如需指定文件：

```bash
python -m IISConfiguration \
  --config /opt/iis/_config/iis.yaml \
  --fallback ./_config/iis_example.yaml
```

IIS 正常运行后，配置页自动以 Blueprint 挂载到 `/admin/config/`，复用 IIS 后台
登录保护。独立模式故意禁止监听非本机地址；需要远程配置时应使用带认证的嵌入页或
SSH 端口转发。

正式配置不存在时，根页面会自动进入五步向导：环境说明、Web/MongoDB、访问令牌、可选
功能、确认保存。向导只收集常用落地参数，最后仍通过完整 `IISSettings` 校验；确认页还会
收集初始管理员密码，在本机运行目录生成临时 `0600` 单行 password-file，并把文件路径写入
无人值守安装命令。密码不进入 YAML、Session 或 HTTP 响应。完成后会引导到 AIClientCenter
自己的配置页。配置已经存在时也可从完整配置页重新打开向导。

## 保存语义

- 引导表单展示 `IISSettings` 的完整默认值；高级模式可直接编辑 YAML。
- 校验复用 `IISConfig.py` 的 Pydantic schema 和运行时投影检查。
- 校验失败不会修改正式文件。
- 保存使用“同目录临时文件 + fsync + `os.replace`”原子替换。
- 旧版本保存在配置目录下 `.config-history/iis/`，默认保留 30 份。
- 密码与 Token 返回浏览器前会替换为保留标记；未改动标记时保存会恢复旧值。
- IIS 配置保存后需要重启服务生效，页面不会擅自重启生产进程。
- 页面只生成配置、临时管理员安装秘密并显示无人值守安装命令；不会创建用户数据库、安装
  依赖、调用 sudo 或操作 systemd。
- 管理员秘密默认位于 `/run/user/<uid>/iis-bootstrap/admin-password-<随机值>`；若该用户运行目录不可用，
  回退到所有者私有的临时目录。目录权限为 `0700`，文件为 `0600`。
- 安装命令带 `--consume-admin-password-file`；installer 成功创建管理员后删除临时秘密，失败
  时保留以便重试。

`IISSettings` 的规范化 JSON Schema 带有一个已审查指纹。schema 发生变化后，如果开发者
尚未审查完整编辑器和首次配置向导，页面会显示兼容性警告，配置页面测试也会失败。

## 字段说明的单一来源

配置项的说明统一写在 `IISConfig.py` 对应 Pydantic `Field(description=...)` 中，不在模板
复制第二套文案。完整编辑器读取同一份 JSON Schema，把说明渲染为字段标题旁可点击、可用
键盘操作的 `!` 帮助弹层；说明字符串以 `iis_msg()` 标记并通过 gettext 目录本地化。CLI 或
未来其他配置前端也应读取 Schema 的 `description`。自动化测试要求每个可编辑叶子字段都有
说明，新增字段遗漏说明时会失败。

## 模块边界

`service.py` 不依赖 Flask，可由安装器、CLI 或测试直接复用。`admin_secret.py` 只负责临时
安装秘密的格式、权限和原子写入，不创建管理员。`web.py` 只包装 HTTP 与 CSRF，并可挂到
任意 Flask 应用。`__main__.py` 是不依赖 IIS 业务启动的独立外壳。

AI Client 的对应页面由 `AIClientCenter.configuration` 和
`AIClientCenter.web.configuration` 提供：独立启动 `python -m AIClientCenter` 后访问
`/config/`；嵌入 IIS 后访问 `/monitor/ai-client-dashboard/config/`。其保存后可调用
AIClientCenter 原有的原子热重载，无需重启 IIS。

## 后续迭代

当前版本先覆盖配置闭环：读取、引导编辑、高级编辑、真实校验、密钥遮蔽、原子保存、
历史恢复和运行态应用。后续可在不改变后端契约的前提下增加连通性检查和 systemd 重启授权；
连通性检查应始终与“语法/结构校验”分开，避免保存
配置时产生第三方 API 费用。
