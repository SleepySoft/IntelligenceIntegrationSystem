import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QToolBar, QAction, QLineEdit,
                             QTabWidget, QVBoxLayout, QWidget, QStatusBar, QProgressBar, QMessageBox)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile, QWebEnginePage
from PyQt5.QtCore import QUrl, QSize, Qt
from PyQt5.QtGui import QIcon

# ================= 配置区域 =================
# 代理服务器地址 (必须带 https://)
PROXY_SERVER = ""
AUTH_USER = ""
AUTH_PASS = ""


# ===========================================

class BrowserEngine(QWebEngineView):
    """
    封装每一个标签页的浏览器视图
    """

    def __init__(self, profile, main_window):
        super().__init__()
        self.main_window = main_window
        # 使用传入的全局 Profile (保持 Session/Cookie)
        page = QWebEnginePage(profile, self)
        self.setPage(page)

        # 绑定代理认证信号 (关键！)
        page.proxyAuthenticationRequired.connect(self.main_window.handle_proxy_auth)

    # 重写 createWindow，支持点击 target="_blank" 链接时在新标签页打开
    def createWindow(self, _type):
        new_tab = self.main_window.add_new_tab()
        return new_tab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Work Browser")  # 伪装标题
        self.resize(1200, 800)

        # 1. 初始化全局 Profile (数据持久化)
        storage_path = os.path.join(os.getcwd(), "browser_data")
        self.profile = QWebEngineProfile("ProSession", self)
        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setCachePath(storage_path)

        # 2. UI 布局初始化
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)  # 让标签栏更像浏览器风格
        self.tabs.setTabsClosable(True)  # 允许关闭标签
        self.tabs.tabCloseRequested.connect(self.close_current_tab)
        self.tabs.currentChanged.connect(self.current_tab_changed)
        self.setCentralWidget(self.tabs)

        # 3. 创建工具栏
        self.create_toolbar()

        # 4. 创建状态栏 (显示链接悬停)
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # 5. 打开第一个标签页
        self.add_new_tab(QUrl("https://www.google.com"), "Home")

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

        # 进度条 (嵌入在地址栏下方或旁边，这里放在 Toolbar 末尾)
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

        browser = BrowserEngine(self.profile, self)
        browser.setUrl(qurl)

        # 绑定事件
        # 1. URL 变化 -> 更新地址栏
        browser.urlChanged.connect(lambda q: self.update_url_bar(q, browser))
        # 2. 加载进度 -> 更新进度条
        browser.loadProgress.connect(self.update_progress)
        # 3. 加载完成 -> 重置进度条
        browser.loadFinished.connect(lambda: self.progress_bar.setValue(100))
        browser.loadFinished.connect(lambda: self.progress_bar.hide())
        browser.loadStarted.connect(lambda: self.progress_bar.show())
        # 4. 标题变化 -> 更新标签文本
        browser.titleChanged.connect(lambda title: self.set_tab_title(browser, title))
        # 5. 图标变化 -> 更新标签图标
        browser.iconChanged.connect(lambda icon: self.set_tab_icon(browser, icon))
        # 6. 状态栏链接悬停信息
        browser.page().linkHovered.connect(lambda l: self.status.showMessage(l))

        i = self.tabs.addTab(browser, label)
        self.tabs.setCurrentIndex(i)

        return browser

    def handle_proxy_auth(self, url, auth, proxy_host):
        """代理自动认证逻辑"""
        # print(f"[Proxy Auth] Sending credentials for {proxy_host}")
        auth.setUser(AUTH_USER)
        auth.setPassword(AUTH_PASS)

    def current_tab_changed(self, i):
        # 切换标签时，更新地址栏显示的 URL
        if self.tabs.count() > 0:
            qurl = self.tabs.currentWidget().url()
            self.url_bar.setText(qurl.toString())
            self.url_bar.setCursorPosition(0)

    def update_url_bar(self, q, browser=None):
        # 只有当 URL 变化的浏览器是当前显示的标签页时，才更新地址栏
        if browser != self.tabs.currentWidget():
            return
        self.url_bar.setText(q.toString())
        self.url_bar.setCursorPosition(0)

    def set_tab_title(self, browser, title):
        index = self.tabs.indexOf(browser)
        # 限制标题长度，防止标签太宽
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
        q = QUrl(self.url_bar.text())
        if q.scheme() == "":
            q.setScheme("https")
        self.tabs.currentWidget().setUrl(q)

    def close_current_tab(self, i):
        if self.tabs.count() < 2:
            return  # 保持至少一个标签页
        self.tabs.removeTab(i)


if __name__ == "__main__":
    # 注入代理参数
    sys.argv.append(f"--proxy-server={PROXY_SERVER}")
    sys.argv.append("--ignore-certificate-errors")

    app = QApplication(sys.argv)

    # 设置应用风格，使用 Fusion 风格会让控件看起来更现代、跨平台一致
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
