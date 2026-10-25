# IIS Operations Dashboard

Linux 运维仪表盘是一个独立组件：每个面板都是 `panels/` 下的脚本，YAML 只负责决定
tmux 窗口、拆分方向和各区域运行哪个脚本。它不依赖 IIS Web 服务。

外部兼容入口为 `Scripts/iis_dashboard.sh`，便于 systemd 和桌面启动项调用；所有实现、
默认配置、面板和测试均位于本目录。

## 配置

程序优先读取 `_config/dashboard.yaml`；文件不存在时使用本目录的 `dashboard.yaml`。
也可通过 `IIS_DASHBOARD_CONFIG` 或命令行 `--config` 指定其他文件。

需要部署机专用布局时，先复制默认配置再修改：

```bash
cp OperationsDashboard/dashboard.yaml _config/dashboard.yaml
```

```yaml
session: iis-monitor

windows:
  wall:
    horizontal:
      - overview
      - vertical:
          - web-log
          - crawler-log
  system: system-monitor
```

- `windows` 下每个键是一个 tmux 窗口，配置顺序也是窗口和快捷键顺序。
- 字符串对应 `panels/<名称>.sh`、`.py` 或同名可执行文件。
- `horizontal` 表示左右拆分，`vertical` 表示上下拆分。
- 每个拆分节点必须正好有两个子项，初次创建时各占 50%。
- 最多配置 9 个窗口，对应 `F1`—`F9` 和 `Alt+1`—`Alt+9`。

每个面板脚本应在前台运行、向 stdout 输出内容并正确响应 `SIGTERM`。脚本自行负责日志
路径、systemd unit、刷新周期和降级行为，不应包含 tmux 布局逻辑。

## 使用

```bash
Scripts/iis_dashboard.sh validate
Scripts/iis_dashboard.sh list-panels
Scripts/iis_dashboard.sh open
Scripts/iis_dashboard.sh attach
Scripts/iis_dashboard.sh status
Scripts/iis_dashboard.sh reload
Scripts/iis_dashboard.sh stop
```

在 tmux 中可用鼠标拖动边框；`Alt+z` 放大或恢复当前面板。按 `F10` 保存当前窗口，或在
外部执行以下命令保存全部窗口：

```bash
Scripts/iis_dashboard.sh save-layout
```

真实尺寸保存到 `_data/dashboard-layouts.json`。记录带有完整配置签名；窗口的脚本或拆分
结构变化后，旧尺寸会被忽略，窗口重新按每次 50% 拆分创建。`reload` 会重建整个 tmux
会话，已连接的客户端会断开，随后重新执行 `open` 或 `attach` 即可。

## 开机启动

现有 `systemd/iis-dashboard.service` 继续调用外部兼容入口。安装方式不变：

```bash
Scripts/install_iis_dashboard.sh
```

systemd 只在后台创建 tmux 会话；桌面启动项负责登录图形桌面后打开终端并附加该会话。
