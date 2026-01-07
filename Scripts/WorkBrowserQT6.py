import sys
import os
import random
import time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QToolBar, QLineEdit,
                             QTabWidget, QVBoxLayout, QWidget, QStatusBar, QProgressBar, QMessageBox)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings, QWebEngineScript
from PyQt6.QtCore import QUrl, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtNetwork import QNetworkProxy

# ================= 配置区域 =================

PROXY_SERVER = ""
AUTH_USER = ""
AUTH_PASS = ""


# ===========================================

class BrowserFingerprint:
    """浏览器指纹工具类"""

    @staticmethod
    def get_random_user_agent():
        """随机选择一个现代的用户代理"""
        user_agents = [
            # Chrome on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",

            # Chrome on macOS
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",

            # Edge
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ]
        return random.choice(user_agents)

    @staticmethod
    def get_anti_detection_script():
        """返回反检测的JavaScript脚本"""
        return """
        // 修改navigator属性以防止被检测为自动化工具
        const modifyNavigator = () => {
            // 删除webdriver属性
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 修改plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
                configurable: true
            });

            // 修改languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                configurable: true
            });

            // 修改platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
                configurable: true
            });

            // 修改vendor
            Object.defineProperty(navigator, 'vendor', {
                get: () => '',
                configurable: true
            });

            // 修改userAgent
            const originalUserAgent = navigator.userAgent;
            Object.defineProperty(navigator, 'userAgent', {
                get: () => originalUserAgent + ' Edg/120.0.0.0',
                configurable: true
            });

            // Chrome运行时属性
            window.chrome = {
                runtime: {},
                loadTimes: function() { return {}; },
                csi: function() { return {}; },
                app: { isInstalled: false },
                webstore: {},
                tabs: {},
                windows: {},
                extension: {},
                runtime: { connect: function() {}, sendMessage: function() {} }
            };

            // 修改document属性
            Object.defineProperty(document, 'hidden', { value: false, configurable: true });
            Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });

            // 修改window属性
            Object.defineProperty(window, 'chrome', { configurable: true, value: window.chrome });
            Object.defineProperty(window, 'outerWidth', { get: () => 1920, configurable: true });
            Object.defineProperty(window, 'outerHeight', { get: () => 1080, configurable: true });

            // 修改屏幕属性
            Object.defineProperty(screen, 'availWidth', { get: () => 1920, configurable: true });
            Object.defineProperty(screen, 'availHeight', { get: () => 1080, configurable: true });
            Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
            Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
        };

        // 执行修改
        modifyNavigator();

        // 覆盖一些常见的检测函数
        (function() {
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // 覆盖getBattery
            if ('getBattery' in navigator) {
                Object.defineProperty(navigator, 'getBattery', {
                    value: () => Promise.resolve({
                        charging: true,
                        chargingTime: 0,
                        dischargingTime: Infinity,
                        level: 1,
                        onchargingchange: null,
                        onchargingtimechange: null,
                        ondischargingtimechange: null,
                        onlevelchange: null
                    }),
                    configurable: true
                });
            }

            // 覆盖媒体设备枚举
            if ('mediaDevices' in navigator && 'enumerateDevices' in navigator.mediaDevices) {
                const originalEnumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                navigator.mediaDevices.enumerateDevices = () => originalEnumerateDevices().then(devices => 
                    devices.map(device => ({
                        deviceId: device.deviceId,
                        groupId: device.groupId,
                        kind: device.kind,
                        label: device.kind === 'videoinput' ? 'Virtual Camera' : 
                               device.kind === 'audioinput' ? 'Virtual Microphone' : 
                               'Virtual Speaker'
                    }))
                );
            }

            // 修改WebGL属性
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) { // UNMASKED_VENDOR_WEBGL
                    return 'Google Inc.';
                }
                if (parameter === 37446) { // UNMASKED_RENDERER_WEBGL
                    return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
                }
                return getParameter.call(this, parameter);
            };
        })();
        """


class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.setup_settings()
        self.inject_anti_detection_scripts()

    def setup_settings(self):
        settings = self.settings()

        # 启用所有现代Web特性
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)

        # 设置字体
        settings.setFontFamily(QWebEngineSettings.FontFamily.StandardFont, "Arial")
        settings.setFontSize(QWebEngineSettings.FontSize.DefaultFontSize, 16)

        # 设置默认编码
        settings.setDefaultTextEncoding("utf-8")

        # 设置未知URL方案策略
        settings.setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.AllowAllUnknownUrlSchemes)

    def inject_anti_detection_scripts(self):
        """注入反检测脚本"""
        script = QWebEngineScript()
        script.setSourceCode(BrowserFingerprint.get_anti_detection_script())
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile().scripts().insert(script)

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        return True


class BrowserEngine(QWebEngineView):
    def __init__(self, profile, main_window):
        super().__init__()
        self.main_window = main_window

        # 使用随机User-Agent
        custom_user_agent = BrowserFingerprint.get_random_user_agent()
        profile.setHttpUserAgent(custom_user_agent)

        # 使用自定义页面
        page = CustomWebEnginePage(profile, self)
        self.setPage(page)

        # 绑定代理认证信号
        page.proxyAuthenticationRequired.connect(self.main_window.handle_proxy_auth)

        # 绑定页面加载完成后的处理
        self.loadFinished.connect(self.on_load_finished)

        # 绑定页面加载开始
        self.loadStarted.connect(self.on_load_started)

    def on_load_started(self):
        # 页面开始加载时，再注入一次脚本确保生效
        if "google.com" in self.url().toString():
            QTimer.singleShot(100, self.inject_anti_detection_on_load)

    def inject_anti_detection_on_load(self):
        """在页面加载时注入反检测脚本"""
        script = BrowserFingerprint.get_anti_detection_script()
        self.page().runJavaScript(script)

    def on_load_finished(self, success):
        if success:
            current_url = self.url().toString()
            if "google.com" in current_url:
                # 针对Google页面，延迟注入额外的修复脚本
                QTimer.singleShot(1000, self.inject_google_fixes)
            if "gemini.google.com" in current_url:
                # 针对Gemini页面，延迟注入修复脚本
                QTimer.singleShot(2000, self.inject_gemini_fixes)

    def inject_google_fixes(self):
        """为Google页面注入额外的修复脚本"""
        script = """
        // 尝试绕过Google的浏览器检测
        try {
            // 隐藏可能存在的浏览器不支持提示
            const unsupportedElements = document.querySelectorAll('[class*="unsupported"], [class*="Unsupported"]');
            unsupportedElements.forEach(el => {
                el.style.display = 'none';
            });

            // 尝试修复登录表单
            const forms = document.querySelectorAll('form');
            forms.forEach(form => {
                form.style.display = 'block';
                form.style.visibility = 'visible';
            });

            // 确保所有输入框可见
            const inputs = document.querySelectorAll('input, textarea, button');
            inputs.forEach(input => {
                input.style.display = '';
                input.style.visibility = 'visible';
                input.style.opacity = '1';
                input.disabled = false;
                input.readOnly = false;
            });
        } catch(e) {}
        """
        self.page().runJavaScript(script)

    def inject_gemini_fixes(self):
        """为Gemini页面注入修复脚本"""
        script = """
        // 针对Gemini页面的修复
        setTimeout(() => {
            // 尝试修复可能的CSS/JS加载问题
            if (document.body) {
                document.body.style.visibility = 'visible';
            }

            // 确保所有元素可见
            const allElements = document.querySelectorAll('*');
            allElements.forEach(el => {
                if (el.style.visibility === 'hidden') {
                    el.style.visibility = 'visible';
                }
                if (el.style.display === 'none') {
                    el.style.display = '';
                }
                if (el.style.opacity === '0') {
                    el.style.opacity = '1';
                }
            });

            // 尝试重新激活可能被禁用的输入框
            const inputs = document.querySelectorAll('input, textarea, [contenteditable="true"]');
            inputs.forEach(input => {
                input.disabled = false;
                input.readOnly = false;
            });

            // 确保聊天区域可见
            const chatElements = document.querySelectorAll('[class*="chat"], [class*="Chat"]');
            chatElements.forEach(el => {
                el.style.display = 'block';
                el.style.visibility = 'visible';
            });
        }, 1000);
        """
        self.page().runJavaScript(script)

    def createWindow(self, _type):
        if _type == QWebEnginePage.WebWindowType.WebBrowserTab:
            new_tab = self.main_window.add_new_tab()
            return new_tab
        return super().createWindow(_type)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Chrome")  # 伪装成Chrome
        self.resize(1400, 900)

        # 1. 初始化全局Profile
        storage_path = os.path.join(os.getcwd(), "browser_data")
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)

        # 使用默认Profile
        self.profile = QWebEngineProfile.defaultProfile()

        # 设置持久化存储路径
        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setCachePath(storage_path)

        # 设置HTTP头
        self.profile.setHttpUserAgent(BrowserFingerprint.get_random_user_agent())
        self.profile.setHttpAcceptLanguage("zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7")

        # 设置持久化Cookie策略
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

        # 2. UI布局
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_current_tab)
        self.tabs.currentChanged.connect(self.current_tab_changed)
        self.setCentralWidget(self.tabs)

        # 3. 创建工具栏
        self.create_toolbar()

        # 4. 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # 5. 打开第一个标签页
        self.add_new_tab(QUrl("https://www.google.com"), "Google")

    def create_toolbar(self):
        nav_tb = QToolBar("Navigation")
        nav_tb.setIconSize(QSize(18, 18))
        self.addToolBar(nav_tb)

        # 后退
        back_btn = QAction("←", self)
        back_btn.setStatusTip("Back")
        back_btn.triggered.connect(lambda: self.tabs.currentWidget().back())
        nav_tb.addAction(back_btn)

        # 前进
        next_btn = QAction("→", self)
        next_btn.setStatusTip("Forward")
        next_btn.triggered.connect(lambda: self.tabs.currentWidget().forward())
        nav_tb.addAction(next_btn)

        # 刷新
        reload_btn = QAction("↻", self)
        reload_btn.setStatusTip("Reload")
        reload_btn.triggered.connect(lambda: self.tabs.currentWidget().reload())
        nav_tb.addAction(reload_btn)

        # 停止
        stop_btn = QAction("✕", self)
        stop_btn.setStatusTip("Stop")
        stop_btn.triggered.connect(lambda: self.tabs.currentWidget().stop())
        nav_tb.addAction(stop_btn)

        # 分隔符
        nav_tb.addSeparator()

        # 地址栏
        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        nav_tb.addWidget(self.url_bar)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setTextVisible(False)
        nav_tb.addWidget(self.progress_bar)

        # 新建标签按钮
        new_tab_btn = QAction("+", self)
        new_tab_btn.triggered.connect(lambda: self.add_new_tab())
        nav_tb.addAction(new_tab_btn)

    def add_new_tab(self, qurl=None, label="New Tab"):
        if qurl is None:
            qurl = QUrl("https://www.google.com")

        # 创建浏览器视图
        browser = BrowserEngine(self.profile, self)
        browser.setUrl(qurl)

        # 绑定事件
        browser.urlChanged.connect(lambda q: self.update_url_bar(q, browser))
        browser.loadProgress.connect(self.update_progress)
        browser.loadFinished.connect(lambda: self.progress_bar.setValue(100))
        browser.loadFinished.connect(lambda: self.progress_bar.hide())
        browser.loadStarted.connect(lambda: self.progress_bar.show())
        browser.titleChanged.connect(lambda title: self.set_tab_title(browser, title))
        browser.iconChanged.connect(lambda icon: self.set_tab_icon(browser, icon))
        browser.page().linkHovered.connect(lambda l: self.status.showMessage(l))

        i = self.tabs.addTab(browser, label)
        self.tabs.setCurrentIndex(i)
        return browser

    def handle_proxy_auth(self, auth_info, proxy_host, proxy_port, authenticator):
        """处理代理认证"""
        if AUTH_USER and AUTH_PASS:
            authenticator.setUser(AUTH_USER)
            authenticator.setPassword(AUTH_PASS)

    def current_tab_changed(self, i):
        if self.tabs.count() > 0:
            qurl = self.tabs.currentWidget().url()
            self.url_bar.setText(qurl.toString())
            self.url_bar.setCursorPosition(0)

    def update_url_bar(self, q, browser=None):
        if browser != self.tabs.currentWidget():
            return
        self.url_bar.setText(q.toString())
        self.url_bar.setCursorPosition(0)

    def set_tab_title(self, browser, title):
        index = self.tabs.indexOf(browser)
        if len(title) > 15:
            title = title[:15] + "..."
        if index != -1:
            self.tabs.setTabText(index, title)

    def set_tab_icon(self, browser, icon):
        index = self.tabs.indexOf(browser)
        if index != -1:
            self.tabs.setTabIcon(index, icon)

    def update_progress(self, p):
        self.progress_bar.setValue(p)

    def navigate_to_url(self):
        url_text = self.url_bar.text().strip()
        if not url_text:
            return

        q = QUrl(url_text)
        if q.scheme() == "":
            q.setScheme("https")
        self.tabs.currentWidget().setUrl(q)

    def close_current_tab(self, i):
        if self.tabs.count() < 2:
            return
        self.tabs.removeTab(i)


def setup_proxy():
    """设置系统代理 - PyQt6版本"""
    if not PROXY_SERVER:
        return

    try:
        # 设置系统环境变量
        os.environ['HTTP_PROXY'] = PROXY_SERVER
        os.environ['HTTPS_PROXY'] = PROXY_SERVER

        # 解析代理服务器地址
        proxy_url = QUrl(PROXY_SERVER)

        # 创建QNetworkProxy对象
        proxy = QNetworkProxy()

        # 根据协议设置代理类型
        if proxy_url.scheme() == "http":
            proxy.setType(QNetworkProxy.ProxyType.HttpProxy)
        elif proxy_url.scheme() == "socks5":
            proxy.setType(QNetworkProxy.ProxyType.Socks5Proxy)
        else:
            proxy.setType(QNetworkProxy.ProxyType.HttpProxy)

        proxy.setHostName(proxy_url.host())
        proxy.setPort(proxy_url.port() if proxy_url.port() > 0 else 8080)

        # 如果需要认证
        if AUTH_USER and AUTH_PASS:
            proxy.setUser(AUTH_USER)
            proxy.setPassword(AUTH_PASS)

        # 为整个应用程序设置代理
        QNetworkProxy.setApplicationProxy(proxy)

        print(f"代理已设置: {PROXY_SERVER}")

    except Exception as e:
        print(f"设置代理时出错: {e}")


if __name__ == "__main__":
    # 在创建QApplication之前设置代理
    setup_proxy()

    # 创建命令行参数
    args = sys.argv

    if PROXY_SERVER:
        args.append(f"--proxy-server={PROXY_SERVER}")

    args.extend([
        "--ignore-certificate-errors",
        "--enable-webgl",
        "--enable-accelerated-2d-canvas",
        "--enable-gpu-rasterization",
        "--enable-oop-rasterization",
        "--enable-smooth-scrolling",
        "--enable-webgl2-compute-context",
        "--enable-gpu",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-web-security",
        "--user-agent=" + BrowserFingerprint.get_random_user_agent(),
        "--disable-dev-shm-usage",
        "--disable-setuid-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--disable-client-side-phishing-detection",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-breakpad",
        "--disable-component-extensions-with-background-pages",
        "--disable-features=Translate,BackForwardCache",
        "--disable-ipc-flooding-protection",
        "--disable-renderer-backgrounding"
    ])

    app = QApplication(args)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
