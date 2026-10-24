# VPS 网站访问统计

生产事实源位于公网入口 VPS `107.175.172.145`。Nginx 只把 HTML 页面导航写入
`/var/log/iis-traffic/pageviews.log`；静态资源、JSON API、查询参数和统计页自身不进入报表。
完整 Nginx access log 仍用于安全审计。GoAccess 1.12 每五分钟从最近 90 天轮转日志重新生成
静态报告，IP 在报告内按 level 2 匿名化，不启动 WebSocket 或额外监听端口。

简洁报告入口为 `https://sleepysoft.org/ops/traffic/`，原始 GoAccess 报告位于
`/ops/traffic/raw/`。VPS 使用 Nginx `auth_request` 把浏览器的
同域 Cookie 交给家庭 IIS `/auth_check`；未登录跳转 `/login?next=/ops/traffic/`，登录成功后
回到报告。鉴权服务不可达时默认失败，不会降级成公开访问。

Cloudflare 代理开启时，必须同步维护 `cloudflare-realip.conf` 中的官方 IP 段。只有受信任的
Cloudflare 网段能通过 `CF-Connecting-IP` 改写客户端地址，防止直连 VPS 时伪造访客 IP。

部署文件对应关系：

- `cloudflare-realip.conf`、`traffic-analytics-http.conf` → `/etc/nginx/conf.d/`
- `sleepysoft-traffic-locations.conf` → `/etc/nginx/snippets/`
- `update-traffic-report` → `/usr/local/sbin/update-iis-traffic-report`
- `build_summary.py` → `/usr/local/lib/iis-traffic/build_summary.py`
- `simple.html` → `/var/www/traffic-analytics/simple.html`
- `iis-traffic.logrotate` → `/etc/logrotate.d/iis-traffic`
- 两个 systemd unit → `/etc/systemd/system/`

`sleepysoft.org` 的 HTTPS server 需在通用家庭转发 location 之前包含：

```nginx
include /etc/nginx/snippets/sleepysoft-traffic-locations.conf;
```

每次修改先执行 `nginx -t`，成功后只 reload，不 restart。
