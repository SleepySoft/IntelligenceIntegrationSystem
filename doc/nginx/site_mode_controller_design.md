# sleepysoft.org 服务/公告切换后台设计

> 状态：设计提案，尚未部署。  
> 目标：家庭服务不可用时，公网入口稳定显示公告；恢复后可以从独立后台安全切回服务，并持续展示健康状态。

## 1. 设计原则

1. **控制面必须留在 VPS**：不能依赖家庭服务器，否则家庭服务故障时后台也会失联。
2. **DDNS 与 Xray 不参与切换**：仅切换主站 `/`；`/ddns/update` 和 `/administrator-manager-api` 始终保持现有路由。
3. **公告页必须完全本地化**：HTML、CSS 和图片都存放在 VPS，不引用家庭侧资源。
4. **切换必须可验证、可回滚**：写配置后先运行 `nginx -t`，成功才 reload；失败则恢复旧状态。
5. **手动优先，自动可选**：维护期间不能因为一次健康检查成功就自动开放服务。

## 2. 推荐的三个运行模式

| 模式 | 行为 | 使用场景 |
| --- | --- | --- |
| `service` | 主站转发家庭设备；502/503/504 自动显示公告 | 日常运行 |
| `announcement` | 主站始终显示公告，不访问家庭设备 | 人工维护、家庭网络长时间不可用 |
| `auto` | 健康时转发；连续失败后公告；连续恢复后重新转发 | 无人值守 |

初始版本建议默认使用 `service` 和 `announcement`；验证监控稳定后再启用 `auto`。

## 3. Nginx 路由结构

保留现有特殊路径，在 `server` 中只把主站 location 抽成可切换 include：

```nginx
location = /ddns/update {
    # 保留现有 DDNS 路由
}

location /administrator-manager-api {
    # 保留现有 Xray WebSocket 路由
}

error_page 502 503 504 =503 /__site_announcement.html;

location = /__site_announcement.html {
    internal;
    root /var/www/site-announcement;
    add_header Cache-Control "no-store" always;
    add_header Retry-After "300" always;
}

include /etc/nginx/site-modes/active.conf;
```

`service.conf`：

```nginx
location / {
    proxy_pass http://home_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;

    proxy_connect_timeout 5s;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    proxy_intercept_errors on;
}
```

`announcement.conf`：

```nginx
location / {
    return 503;
}
```

公告文件固定保存为 `/var/www/site-announcement/__site_announcement.html`。服务模式中的 upstream 错误和公告模式中的主动 `503` 都会落到这个内部 URI，并统一保持 HTTP 503 状态。

`active.conf` 是指向其中一个文件的符号链接。切换流程：

1. 记录当前链接目标；
2. 原子替换 `active.conf`；
3. 执行 `nginx -t`；
4. 成功则 `systemctl reload nginx`；
5. 失败则恢复旧链接并记录错误。

即使处于 `service` 模式，家庭 upstream 返回 502/503/504 时也会立即落到本地公告页。这解决“模式还没来得及切换，访客先看到 504”的问题。

## 4. 控制后台

建议实现为独立、轻量的 Python 服务，例如：

```text
/opt/site-controller/
  app.py
  monitor.py
  templates/
  static/
/var/lib/site-controller/state.json
/var/log/site-controller/controller.log
```

只监听 `127.0.0.1:9100`。第一阶段通过 SSH 端口转发访问：

```bash
ssh -L 9100:127.0.0.1:9100 root@107.175.172.145
```

浏览器打开 `http://127.0.0.1:9100`。不要直接把后台暴露到公网；未来需要随时访问时，可增加 Tailscale 或 Cloudflare Access。

后台首页应显示：

- 当前模式与实际生效配置；
- 当前家庭公网 IP、最后一次 DDNS 上报时间；
- TCP、HTTP 和应用级健康状态；
- 最近成功时间、连续失败/成功次数、延迟；
- Nginx 与 home-ddns 服务状态；
- 正式站点证书剩余天数；
- 最近若干次切换记录与操作者；
- 公告标题、正文、预计恢复时间的编辑与预览。

操作按钮：

- “切到家庭服务”
- “发布公告”
- “启用自动模式”
- “立即重新检测”

切换操作使用 POST，请求需要 CSRF 防护，并要求再次确认。状态查询与切换 API 分离。

## 5. 健康检查与状态机

健康检查必须从 VPS 直接访问 `home_backend`，不能访问公开的 `sleepysoft.org`，否则会绕经 Cloudflare并形成错误判断。

建议家庭主应用提供无需登录、开销很小的 `/healthz`：

```json
{"status":"ok","service":"intelligence-hub"}
```

检测层级：

1. 当前 DDNS 上报是否过期，例如超过 3 分钟；
2. `home_backend:25000` TCP 是否可连接；
3. `GET /healthz` 是否在 5 秒内返回 200；
4. 可选：主应用所依赖的 MongoDB/核心线程是否健康。

防抖建议：

- 每 15 秒检测一次；
- 连续 3 次失败才判定 DOWN；
- 连续 3 次成功才判定 UP；
- `announcement` 模式永不自动恢复；
- `auto` 模式才根据 UP/DOWN 自动切换；
- 每次自动切换后设置至少 5 分钟冷却期，避免抖动。

状态机：

```text
service --连续失败--> service_degraded --再次失败--> announcement(auto)
announcement(auto) --连续成功+冷却完成--> service
announcement(manual) --只能人工确认--> service
```

## 6. 公告页行为

公告页建议返回 HTTP `503 Service Unavailable`，并附带：

```http
Cache-Control: no-store
Retry-After: 300
```

页面内容至少包括：

- 简短状态说明；
- 故障/维护开始时间；
- 最近更新时间；
- 预计恢复时间（可空）；
- “系统正在恢复，请稍后重试”的明确提示。

不要在公告中显示家庭公网 IP、内部端口、堆栈信息或监控详情。

## 7. 权限与安全

控制服务不应以 root 运行。建议创建 `site-controller` 用户，并只授予：

- 写入 `/etc/nginx/site-modes/active.conf` 所在目录的受控权限；
- 更新 `/var/www/site-announcement/`；
- 通过严格 sudoers 规则运行固定的 `nginx -t` 和 `systemctl reload nginx`；
- 只读查询 Nginx/home-ddns 状态。

同时改造 DDNS：

- Token 从 query string 移到 `X-DDNS-Token`；
- `/ddns/update` 关闭常规 access log；
- 轮换已进入历史日志的旧 Token；
- 状态文件只记录最后 IP、时间和结果，不记录 Token。

## 8. 推荐实施顺序

### 第一阶段：无后台的可靠公告切换

1. 创建本地公告页；
2. 拆分 `service.conf`、`announcement.conf`；
3. 加入 upstream 502/503/504 公告回退；
4. 提供经过验证的命令行切换脚本；
5. 验证 DDNS 与 Xray 路由不受影响。

### 第二阶段：本机控制后台

1. 实现只监听 loopback 的后台；
2. 展示模式、DDNS、证书及健康状态；
3. 支持编辑公告、预览和人工切换；
4. 记录审计日志；
5. 通过 SSH 隧道访问。

### 第三阶段：自动模式与通知

1. 增加健康状态机和防抖；
2. 增加自动切换；
3. 通过邮件、Webhook 或其他渠道通知故障与恢复；
4. 进行断网、超时、错误响应和恢复演练。

## 9. 验收条件

- 家庭设备断网时，访客在合理时间内看到 VPS 本地公告，而不是 Cloudflare/Nginx 504。
- 公告模式下 DDNS 上报与 Xray WebSocket 不受影响。
- 家庭服务恢复后，可在后台看到连续健康检查成功并一键恢复。
- `nginx -t` 失败时不会破坏当前可用配置。
- 控制后台不向公网开放，所有切换均有时间与结果记录。
- 公告页不被 Cloudflare 长时间缓存。
