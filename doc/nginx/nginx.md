这份文档旨在描述 **sleepysoft.org** 的系统架构、Nginx 配置逻辑及运维要点。文档结构清晰，包含上下文信息，可直接用于指导开发或作为 AI 辅助开发的上下文输入。

---

# 系统架构与 Nginx 配置技术文档

## 1. 需求与架构概述

本系统采用 **反向代理 + 内网穿透** 的混合架构，旨在通过公网 VPS 安全地访问部署在家庭 Windows 服务器上的多个内部应用（主站、向量数据库、爬虫监控等），并实现统一的身份认证和日志审计。

### 流量路径

```mermaid
用户 (Browser) 
  --> [节点 A: Linux VPS (公网入口)] 
      --> (HTTPS 转发) 
      --> [家用路由器 (端口映射 25000 -> 8443)] 
          --> [节点 B: Windows Server (应用网关)] 
              --> [本地应用 (Python/Flask等)]

```

### 核心需求

1. **统一入口**：通过 `sleepysoft.org` 访问所有服务。
2. **真实 IP 透传**：Windows 端必须能获取用户的真实公网 IP，而非 VPS 的 IP。
3. **集中鉴权**：所有子服务（如 VectorDB）无需单独实现登录，统一由 Nginx 委托主服务（Port 5000）进行鉴权。
4. **Windows 服务化**：Windows 端 Nginx 需作为系统服务自动启动。

---

## 2. 节点 A：Linux VPS (公网网关)

* **主机名**：`sleepysoft.org`
* **IP**：`107.175.172.145` (作为下游的授信 IP)
* **角色**：SSL 卸载、流量入口、第一层反向代理。

### 配置原理

配置文件位于 `/etc/nginx/sites-available/sleepysoft.org`。

1. **HTTPS 强制跳转**：所有 HTTP 请求 301 重定向至 HTTPS。
2. **透明转发**：
* `proxy_pass https://sleepysoft.asuscomm.com:25000;`：将流量转发至家庭路由器的公网端口。
* `proxy_set_header Host $host;`：**关键**。保留原始域名 `sleepysoft.org` 传递给下游，防止下游因域名不匹配拒绝服务。
* `proxy_set_header X-Real-IP $remote_addr;`：将用户真实 IP 写入请求头。


3. **SSL 握手优化**：配置 `proxy_ssl_server_name on;` 以支持 SNI，确保护手成功。

---

## 3. 节点 B：Windows Server (应用网关)

* **主机名**：`sleepysoft.asuscomm.com` (DDNS)
* **本地 IP**：`127.0.0.1`
* **角色**：应用路由、统一鉴权、日志审计。
* **运行方式**：通过 **WinSW** 注册为 Windows 服务 (`nginx-service.exe`)。

### 配置原理

配置文件位于 `C:\nginx\conf\nginx.conf`。

#### 3.1 核心模块设置

* **Real IP 模块 (IP 还原)**：
* `set_real_ip_from 107.175.172.145;`：信任 VPS 的 IP。
* `real_ip_header X-Forwarded-For;`：从该头中递归剥离 VPS IP，还原用户真实 IP。
* *目的*：确保日志 (`access_main.log`) 和后端应用获取到的是真实公网 IP。


* **日志格式**：自定义 `goaccess_ext`，记录 `request_time` (整体耗时) 和 `upstream_response_time` (后端耗时)，便于性能分析。

#### 3.2 站点配置 (Server 8443)

* **监听端口**：监听 `8443` (SSL)。路由器将外部 `25000` 映射至此。
* **域名匹配**：`server_name sleepysoft.asuscomm.com sleepysoft.org;` 同时响应 DDNS 域名和 VPS 转发过来的域名。
* **统一鉴权 (`auth_request`)**：
* 定义内部接口 `location = /_auth_check`，代理至 `127.0.0.1:5000/auth_check`。
* 在子服务 location 中使用 `auth_request /_auth_check;`。
* *原理*：Nginx 发起子请求，若返回 2xx 则放行，401/403 则拦截并跳转登录页。



#### 3.3 路由表

| URL 路径 | 转发目标 (Upstream) | 特性说明 |
| --- | --- | --- |
| `/` | `127.0.0.1:5000` | 主服务，包含登录逻辑。 |
| `/vectordb/` | `127.0.0.1:8001/` | **新增**。路径剥离 (Strip Prefix)，传递 `X-Forwarded-Prefix`。 |
| `/crawl_monitor/` | `127.0.0.1:8002/` | 路径剥离，受统一鉴权保护。 |

---

## 4. 运维指南

### Linux VPS

* **配置文件路径**：`/etc/nginx/sites-available/sleepysoft.org`
* **重载配置**：`sudo nginx -s reload` 或 `sudo systemctl reload nginx`
* **日志路径**：`/var/log/nginx/access.log`

### Windows Server

* **安装目录**：`C:\nginx\`
* **配置文件路径**：`C:\nginx\conf\nginx.conf`
* **服务管理**：
* 启动：`net start nginx` (或在服务管理器中启动 `Nginx Service`)
* 停止：`net stop nginx`
* **重载配置 (推荐)**：在 Nginx 目录下运行 `nginx.exe -s reload`


* **日志路径**：`C:\nginx\logs\` (按业务分为 `access_main.log`, `access_vectordb.log` 等)

### 常见问题排查

1. **无限重定向**：检查 VPS 的 `X-Forwarded-Proto` 是否正确传递，以及 Windows 后端应用是否根据该头判断 HTTPS。
2. **IP 显示为 VPS IP**：检查 Windows 配置中 `set_real_ip_from` 是否包含 VPS 的最新公网 IP。
3. **子服务 404**：检查 `proxy_pass` 末尾是否遗漏了 `/` (用于剥离路径前缀)。
