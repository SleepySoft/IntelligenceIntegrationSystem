这份部署记录总结了基于 PyQt5 实现“无客户端直连 HTTPS 代理”的完整方案。方案核心是使用 standard HTTP proxy over TLS 协议。

**目标**：在 VPS 上部署 Xray 作为 HTTPS 代理（端口 8443），与现有的 Nginx（端口 443）共存，共用 Let's Encrypt 证书。客户端为 Python 程序，不依赖本地 v2ray 核心。

---

### 1. 环境准备

* **VPS**: Ubuntu 系统。
* **证书**: 已由 Nginx/Certbot 申请，路径在 `/etc/letsencrypt/live/你的域名/`。
* **DNS**: 必须将域名解析设为 **DNS Only（灰色云朵）**，**严禁开启** Cloudflare 的 CDN 代理（橙色云朵），否则连接会被拦截返回 400 错误。

### 2. 服务端部署 (Xray)

**安装 Xray**
使用 root 执行官方脚本：
`bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"`

**修改配置**
编辑 `/usr/local/etc/xray/config.json`，填入以下内容。注意修改**端口**、**账号密码**和**证书路径**：

```json
{
  "log": { "loglevel": "warning", "access": "none", "error": "none" },
  "inbounds": [
    {
      "port": 8443,
      "protocol": "http",
      "settings": {
        "auth": "password",
        "accounts": [
          { "user": "admin", "pass": "你的强密码" }
        ],
        "allowTransparent": false
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "certificates": [
            {
              "certificateFile": "/etc/letsencrypt/live/你的域名/fullchain.pem",
              "keyFile": "/etc/letsencrypt/live/你的域名/privkey.pem"
            }
          ],
          "alpn": ["http/1.1"]
        }
      },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls"] }
    }
  ],
  "outbounds": [{ "protocol": "freedom" }]
}

```

*注意：`"alpn": ["http/1.1"]` 是兼容浏览器的关键，不可省略。*

### 3. 权限与服务修正 (关键步骤)

由于证书属于 root，必须强制 Xray 以 root 身份运行，并清除安装脚本自带的权限锁定。

**执行以下命令序列：**

1. **删除锁定文件**（解决 User=nobody 无法修改的问题）：
`rm -f /etc/systemd/system/xray.service.d/10-donot_touch_single_conf.conf`
2. **修改服务用户为 Root**：
执行 `systemctl edit xray`，粘贴以下内容覆盖配置：
```ini
[Service]
User=root
Group=root
CapabilityBoundingSet=
AmbientCapabilities=

```


3. **重启服务**：
`systemctl daemon-reload && systemctl restart xray`
4. **检查状态**：
`systemctl status xray` (应显示 active running)。

### 4. 客户端实现 (Python/PyQt5)

Python 端直接调用 Chromium 内核参数连接。

**核心代码逻辑**：

```python
import sys
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

# 配置
PROXY = "https://你的域名:8443" # 必须带 https://
USER = "admin"
PWD = "你的强密码"

class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.browser = QWebEngineView()
        self.page = QWebEnginePage()
        # 绑定认证信号，自动填充密码
        self.page.proxyAuthenticationRequired.connect(
            lambda url, auth, proxy: (auth.user(USER), auth.password(PWD))
        )
        self.browser.setPage(self.page)
        self.setCentralWidget(self.browser)
        self.browser.setUrl(QUrl("https://www.google.com"))

if __name__ == "__main__":
    # 注入代理参数，必须在 QApplication 实例化前
    sys.argv.append(f"--proxy-server={PROXY}")
    sys.argv.append("--ignore-certificate-errors") # 调试时可选
    
    app = QApplication(sys.argv)
    win = Browser()
    win.show()
    sys.exit(app.exec_())

```

### 5. 故障排查速查

* **客户端白屏/无法连接**：检查 Cloudflare 是否关闭了小黄云（必须直连 IP）。
* **服务端报错 permission denied**：检查是否删除了 `10-donot_touch...` 文件并重启了服务。
* **curl 报错 HTTP/2 framing layer**：检查配置文件是否加了 `"alpn": ["http/1.1"]`。
* **测试命令**：`curl -v -k -x https://user:pass@你的域名:8443 https://www.google.com`
