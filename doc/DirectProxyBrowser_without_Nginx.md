这份文档适用于：**全新的、未安装 Nginx** 的 Ubuntu VPS。
目标：部署 Xray 监听 443 端口，实现 Python 浏览器无客户端直连。

---

### 1. 域名解析准备

* 登录域名服务商，将 `你的域名` A 记录解析到 VPS IP。
* **注意**：如果是 Cloudflare，必须设为 **DNS Only** (灰色云朵)，**严禁**开启 CDN 代理。

### 2. 环境安装与证书申请

依次执行以下命令（Root用户）：

```bash
# 1. 安装基础工具
apt update && apt install -y curl socat

# 2. 安装 acme.sh 证书工具
curl https://get.acme.sh | sh
source ~/.bashrc

# 3. 申请证书 (Standalone模式，临时占用80端口)
# 替换下方域名
acme.sh --issue --standalone -d 你的域名 --ecc

```

### 3. 安装 Xray 并部署证书

```bash
# 1. 安装 Xray 核心
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"

# 2. 创建证书存放目录
mkdir -p /usr/local/etc/xray/cert

# 3. 将证书安装到 Xray 目录 (配置自动续期钩子)
# 替换下方域名
acme.sh --install-cert -d 你的域名 --ecc \
--key-file       /usr/local/etc/xray/cert/private.key  \
--fullchain-file /usr/local/etc/xray/cert/fullchain.crt \
--reloadcmd      "systemctl restart xray"

# 4. 放行证书权限
chmod 644 /usr/local/etc/xray/cert/*

```

### 4. 配置 Xray (监听 443)

编辑配置文件：`nano /usr/local/etc/xray/config.json`
**清空原有内容，粘贴以下配置**（注意修改用户和密码）：

```json
{
  "log": { "loglevel": "warning", "access": "none", "error": "none" },
  "inbounds": [
    {
      "port": 443,
      "protocol": "http",
      "settings": {
        "auth": "password",
        "accounts": [
          { "user": "myadmin", "pass": "mypassword" } 
        ],
        "allowTransparent": false
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "certificates": [
            {
              "certificateFile": "/usr/local/etc/xray/cert/fullchain.crt",
              "keyFile": "/usr/local/etc/xray/cert/private.key"
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

### 5. 修正服务权限 (至关重要)

必须强制 Xray 以 root 运行以读取证书，防止 Systemd 自动降权。

```bash
# 1. 删除官方脚本生成的锁定文件
rm -f /etc/systemd/system/xray.service.d/10-donot_touch_single_conf.conf

# 2. 覆盖服务配置
# 执行后会打开编辑器，粘贴下方内容并保存 (Ctrl+O, Enter, Ctrl+X)
systemctl edit xray

```

**粘贴内容：**

```ini
[Service]
User=root
Group=root
CapabilityBoundingSet=
AmbientCapabilities=

```

### 6. 启动服务

```bash
systemctl daemon-reload
systemctl restart xray
systemctl status xray

```

*状态应显示绿色 active (running)*

---

### 客户端连接参数 (Python)

无需端口号，默认走 443。

```python
# Python 启动参数
PROXY_ARG = "--proxy-server=https://你的域名" 

# 代码逻辑
# auth.setUser("myadmin")
# auth.setPassword("mypassword")

```