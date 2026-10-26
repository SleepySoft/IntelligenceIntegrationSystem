# sleepysoft.org VPS 运维入口

> 主机：`107.175.172.145`（`racknerd-9a15334`）  
> 最后核对：2026-10-13  
> 重要：本文不保存 SSH 密码、DDNS Token、VLESS UUID 或私钥。

## 一、当前部署

```text
访客 -> Cloudflare -> 本 VPS Nginx :443
                        |- /ddns/update -> 127.0.0.1:9000 home-ddns
                        |- /administrator-manager-api -> 127.0.0.1:10000 Xray
                        `- 其他路径 -> home_backend -> 家庭公网 IP:25000
```

- Nginx：`80/443`，配置位于 `/etc/nginx/`。
- 主站：`/etc/nginx/sites-enabled/sleepysoft.org`。
- 另一个站点：`/etc/nginx/sites-enabled/impart.today`。
- 动态家庭上游：`/etc/nginx/conf.d/home_upstream.conf`。
- DDNS 服务：`/opt/home-ddns/home_ddns.py`，监听 `127.0.0.1:9000`。
- Xray：`/usr/local/etc/xray/config.json`，VLESS WebSocket，监听 `127.0.0.1:10000`。

2026-10-13 检查时，家庭设备每分钟上报 `223.166.145.161`，Nginx 使用：

```nginx
upstream home_backend {
    server 223.166.145.161:25000;
}
```

## 二、当前已知问题

1. VPS 到 `223.166.145.161:25000` 可以建立连接，但家庭侧 Nginx 返回 `504 Gateway Time-out`。主要故障在家庭入口后面的内部应用链路，不在公网 VPS。
2. `sleepysoft.asuscomm.com` 当时解析到 `223.166.145.200`，与自建 DDNS 上报的 `.161` 不同；`.200:25000` 从 VPS 连接超时。当前应以自建 DDNS 上报为准。
3. `sleepysoft.org` 证书有效至 2027-01-05；`impart.today` 有效至 2026-11-27。
4. Certbot 显示 failed，是两个废弃域名 `google.sleepysoft.org`、`gemini.google.sleepysoft.org` 的过期证书续期失败造成的。
5. Ubuntu 20.04 已结束标准支持；内核和 Nginx 较旧。
6. DDNS Token 当前放在 URL query string，会进入 Nginx access log；应轮换并改成请求头，同时关闭该 location 的敏感访问日志。

## 三、服务/公告切换功能（已于 2026-10-14 部署）

这是一个**独立运行在 VPS 上的小程序**，不依赖家庭设备。家庭设备不可用时，控制后台仍可访问。

建议程序：

- 服务：`site-controller.service`
- 程序：`/opt/site-controller/app.py`
- 配置：`/etc/site-controller/config.json`
- 状态：`/var/lib/site-controller/state.json`
- 审计：`/var/log/site-controller/audit.log`
- 监听：`127.0.0.1:9100`
- 访问方式：从管理电脑建立 SSH 隧道：

```bash
ssh -L 9100:127.0.0.1:9100 root@107.175.172.145
```

然后浏览器打开 `http://127.0.0.1:9100`。

后台负责：

- 显示家庭 IP、最后 DDNS 上报时间、健康状态、延迟和证书期限；
- 编辑公告标题、正文、开始时间和预计恢复时间；
- 在“家庭服务”“公告”“自动”三种模式间切换；
- 调用 `nginx -t` 验证配置，通过后 reload；失败则回滚；
- 记录每次人工/自动切换。

## 四、Nginx 怎样切换 routing（已部署）

只切换主站 `/`，以下两个路径始终保留：

- `/ddns/update`
- `/administrator-manager-api`

当前目录：

```text
/etc/nginx/site-modes/service.conf
/etc/nginx/site-modes/announcement.conf
/etc/nginx/site-modes/active.conf -> 上面二者之一
/var/www/site-announcement/__site_announcement.html
```

主站 `server` 中写：

```nginx
error_page 502 503 504 =503 /__site_announcement.html;

location = /__site_announcement.html {
    internal;
    root /var/www/site-announcement;
    add_header Cache-Control "no-store" always;
    add_header Retry-After "300" always;
}

include /etc/nginx/site-modes/active.conf;
```

`service.conf` 包含正常家庭转发，并启用错误回退：

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

`announcement.conf` 强制返回公告：

```nginx
location / {
    return 503;
}
```

切换就是原子改变 `active.conf` 的符号链接，然后：

```bash
nginx -t && systemctl reload nginx
```

在 `service` 模式下，家庭端如果返回 502/503/504，Nginx 也会自动显示 VPS 本地公告，因此不必等待人工切换。

## 五、怎样切换

首选：进入 `site-controller` 后台点击：

- **切到家庭服务**：将 `active.conf` 指向 `service.conf`。
- **发布公告**：将 `active.conf` 指向 `announcement.conf`。
- **启用自动模式**：连续 3 次健康检查失败后切公告；连续 3 次成功且冷却期结束后切回服务。

不要直接手改 `sites-enabled/sleepysoft.org` 或符号链接来回切换。后台会先执行 `nginx -t`，成功才 reload，并在失败时自动回滚。

## 六、怎样更改发布页

首选：在后台编辑并预览，确认后原子写入：

```text
/var/www/site-announcement/__site_announcement.html
```

公告页完全存放在 VPS，不引用家庭设备资源。应显示：

- 当前为维护/故障状态；
- 开始时间、最近更新时间；
- 预计恢复时间（可空）；
- 稍后重试提示。

公告响应保持 HTTP 503，并包含：

```http
Cache-Control: no-store
Retry-After: 300
```

不要在公告页透露家庭公网 IP、端口、内部错误或监控详情。

管理员用户名为 `admin`。密码不写入本文；首次部署时单独交付。需要更换密码时，应重新生成 `/etc/site-controller/config.json` 中的 PBKDF2 哈希，而不是保存明文密码。

## 七、健康监控（计划）

从 VPS 直接检测 `home_backend`，不要请求公开的 `sleepysoft.org`，避免绕经 Cloudflare。

建议每 15 秒检查：

1. DDNS 上报是否超过 3 分钟；
2. `家庭 IP:25000` 是否可连接；
3. `GET /healthz` 是否在 5 秒内返回 200；
4. 连续 3 次失败判定 DOWN，连续 3 次成功判定 UP；
5. 自动切换后冷却至少 5 分钟，避免反复抖动。

`announcement` 手动模式不自动恢复；只有 `auto` 模式会自动切回。

## 八、下次登录先执行的只读检查

```bash
systemctl --no-pager --failed
systemctl status nginx home-ddns xray --no-pager -l
cat /etc/nginx/conf.d/home_upstream.conf
journalctl -u home-ddns -n 30 --no-pager -l
tail -n 50 /var/log/nginx/error.log
nginx -T
```

完整本地设计文档位于项目仓库：

```text
doc/nginx/vps-107.175.172.145.md
doc/nginx/site_mode_controller_design.md
```
