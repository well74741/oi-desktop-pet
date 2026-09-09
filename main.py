"""桌宠程序主入口"""

import sys
import os
import time
import datetime
import threading
import traceback


from module_core import APP_VERSION


def _pip_install(pkgs):
    """调用 pip 安装缺失依赖。"""
    try:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install"] + pkgs,
                       timeout=180)
        return True
    except Exception:
        return False


def _apply_system_theme(app):
    """统一给应用设置深色 Fusion 调色板，窗口/弹窗与桌宠风格一致。"""
    try:
        app.setStyle("Fusion")
        from PyQt5.QtGui import QPalette, QColor
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(30, 36, 48))
        pal.setColor(QPalette.WindowText, QColor(213, 219, 232))
        pal.setColor(QPalette.Base, QColor(31, 38, 52))
        pal.setColor(QPalette.AlternateBase, QColor(36, 43, 58))
        pal.setColor(QPalette.ToolTipBase, QColor(35, 42, 58))
        pal.setColor(QPalette.ToolTipText, QColor(213, 219, 232))
        pal.setColor(QPalette.Text, QColor(213, 219, 232))
        pal.setColor(QPalette.Button, QColor(35, 42, 58))
        pal.setColor(QPalette.ButtonText, QColor(213, 219, 232))
        pal.setColor(QPalette.BrightText, QColor(255, 90, 90))
        pal.setColor(QPalette.Highlight, QColor(74, 144, 226))
        pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        pal.setColor(QPalette.PlaceholderText, QColor(127, 138, 160))
        app.setPalette(pal)
    except Exception:
        pass


def _check_dependencies():
    """启动时检查必装依赖（PyQt5 / PyYAML / Pillow），缺失时提示安装。"""
    missing = []
    for mod in ("PyQt5", "yaml", "PIL"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not missing:
        return True
    pkgs = {"PyQt5": "PyQt5", "yaml": "PyYAML", "PIL": "Pillow"}
    names = [pkgs[m] for m in missing]
    cmd = '"%s" -m pip install %s' % (sys.executable, " ".join(names))
    if "PyQt5" in missing:
        # 没有 PyQt5 无法弹 Qt 窗口，用系统对话框提示
        try:
            import ctypes
            ret = ctypes.windll.user32.MessageBoxW(
                0,
                "桌宠缺少必装依赖：%s\n\n请运行安装：\n%s\n\n是否立即尝试安装？"
                % (", ".join(names), cmd),
                "oi桌宠 - 缺少依赖", 0x24)
            if ret == 6:  # IDYES
                _pip_install(names)
        except Exception:
            print("缺少依赖：%s，请运行 %s" % (", ".join(names), cmd))
        return False
    # PyQt5 可用：用 Qt 弹窗提示
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv)
    ret = QMessageBox.question(
        None, "缺少依赖",
        "桌宠缺少必装依赖：%s\n\n安装命令：\n%s\n\n是否立即安装？"
        % (", ".join(names), cmd))
    if ret == QMessageBox.Yes:
        _pip_install(names)
        if not _check_dependencies():
            QMessageBox.warning(None, "安装未完成",
                                "仍有依赖缺失，请手动运行：\n%s" % cmd)
            return False
        return True
    return False


DEFAULT_CONFIG = {
    "pet": {
        "size": 75,
        "opacity": 1.0,
        "image": "assets/yxm.webp",   # 默认桌宠图标：内置动图
    },
    "appearance": {
        "icon": "assets/icon.ico",
    }
}


def load_config(path: str = "config.yaml") -> dict:
    config = DEFAULT_CONFIG.copy()

    if not os.path.exists(path):
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0] if not getattr(sys, 'frozen', False) else sys.executable))
        path = os.path.join(base_dir, "config.yaml")

    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                user_config = yaml.safe_load(f) or {}
                for key, value in user_config.items():
                    if isinstance(value, dict) and key in config:
                        config[key].update(value)
                    else:
                        config[key] = value
        except Exception as e:
            print(f"配置加载失败: {e}")

    return config


def resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class DesktopPetApp:

    def __init__(self):
        # 尺寸档位（设置窗口"桌宠外观"）：气泡大小 / 桌宠大小（含径向菜单）。
        # 气泡用 kit.bs()、桌宠用 kit.ps() 内部缩放；QT_SCALE_FACTOR 固定 1.5
        # 作为全局基准（低缩放屏放大到 150% 基准），必须在 QApplication 创建前设置。
        try:
            import pet_gravity as _pg
            _st = _pg.load_settings()
            os.environ["QT_SCALE_FACTOR"] = "1.5"
            from widgets import kit as _kit
            _kit.set_bubble_scale(float(_st.get("bubble_scale", 1.0) or 1.0))
            _kit.set_pet_scale(float(_st.get("pet_scale", 1.0) or 1.0))
        except Exception:
            try:
                os.environ["QT_SCALE_FACTOR"] = "1.5"
            except Exception:
                pass
        # 任务管理器/托盘识别：统一应用身份与 oi.png 图标
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('oi桌宠')
            except Exception:
                pass
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)  # QtWebEngine（网页聊天悬浮窗）
        self.app = QApplication(sys.argv)
        # 统一弹窗字体与按钮：QMessageBox/QInputDialog 默认 9pt 偏大、按钮无形状——
        # 用全局 QSS 统一到 11px 正文 + kit 规范按钮形状（边框/圆角/适中尺寸）。
        try:
            from widgets import kit
            _btn = kit.DIALOG_BTN_QSS
            self.app.setStyleSheet(
                "QMessageBox{font-size:11px;} QMessageBox QLabel{font-size:11px;}"
                "QInputDialog{font-size:11px;} QInputDialog QLabel{font-size:11px;}"
                "QInputDialog QLineEdit,QInputDialog QPlainTextEdit{font-size:11px;}"
                "QMessageBox QPushButton,QInputDialog QPushButton{%s}" % _btn)
        except Exception:
            pass
        self.app.setApplicationName("oi桌宠")
        self.app.setApplicationDisplayName("oi桌宠")
        app_icon_path = resource_path("assets/oi.png")
        if os.path.exists(app_icon_path):
            self.app.setWindowIcon(QIcon(app_icon_path))
        self.app.setQuitOnLastWindowClosed(False)
        _apply_system_theme(self.app)
        # QtWebEngine 内核(Chromium 83)过旧：首次使用前伪装新版 Chrome UA，
        # 减少"浏览器版本过低"提示（真正依赖新内核的站点请用"外开"按钮）
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineProfile
            QWebEngineProfile.defaultProfile().setHttpUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        except Exception:
            pass

        self.config = load_config()
        self.pet = GravityPet(self.config)
        # 桌宠右键“退出”走与托盘退出一致的完整退出流程
        self.pet.quit_requested = self._quit

        self.tray_icon = None
        self._setup_tray()
        self.pet.show()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # 托盘图标统一用 oi.png 图案（不加载自定义 icon.ico）
        pet_icon = resource_path("assets/oi.png")
        icon_path = self.config.get("appearance", {}).get("icon", "assets/icon.ico")
        icon_full = resource_path(icon_path)
        if os.path.exists(pet_icon):
            icon = QIcon(pet_icon)
        elif os.path.exists(icon_full):
            icon = QIcon(icon_full)
        elif os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = QIcon.fromTheme("application-icon")

        self.tray_icon = QSystemTrayIcon(icon, self.app)
        self.tray_icon.setToolTip("oi桌宠 v%s - 点击显示/隐藏" % APP_VERSION)

        menu = QMenu()
        show_action = QAction("显示桌宠", self.app)
        show_action.triggered.connect(self._show_pet)
        menu.addAction(show_action)

        hide_action = QAction("隐藏桌宠", self.app)
        hide_action.triggered.connect(self._hide_pet)
        menu.addAction(hide_action)

        menu.addSeparator()

        quit_action = QAction("退出", self.app)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_click)
        self.tray_icon.show()

    def _show_pet(self):
        self.pet.show()
        self.pet.raise_()

    def _hide_pet(self):
        self.pet.hide()

    def _on_tray_click(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            if self.pet.isVisible():
                self.pet.hide()
            else:
                self._show_pet()

    def _quit(self):
        self.pet.close()
        if self.tray_icon:
            self.tray_icon.hide()
        self.app.quit()

    def run(self):
        return self.app.exec_()


def main():
    # 把 Qt/yaml/桌宠模块提升为模块级全局，供 DesktopPetApp/load_config 使用
    global yaml, Qt, QLockFile, QApplication, QSystemTrayIcon, QMenu, QAction, QIcon
    global GravityPet
    if not _check_dependencies():
        sys.exit(1)
    import yaml
    from PyQt5.QtCore import Qt, QLockFile
    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
    from PyQt5.QtGui import QIcon
    from pet_gravity import GravityPet

    import atexit
    import tempfile
    # 全局异常钩子：所有未捕获异常写入错误日志，方便排查闪退
    _log_path = os.path.join(tempfile.gettempdir(), "oi_pet_error.log")

    def _hook(etype, value, tb):
        try:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write("\n==== %s ====\n" % datetime.datetime.now().isoformat())
                traceback.print_exception(etype, value, tb, file=f)
        except Exception:
            pass
        traceback.print_exception(etype, value, tb)

    sys.excepthook = _hook
    lock_path = os.path.join(tempfile.gettempdir(), "oi_pet.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(1000)
    if not lock.tryLock(500):
        return   # 已有实例在运行：直接退出（单实例）
    def _cleanup_lock():
        try:
            lock.unlock()
        except Exception:
            pass

    def _kill_child_processes():
        """退出前杀掉会锁住 _MEI 解包目录的子进程（QtWebEngineProcess 等），
        否则 PyInstaller onefile 退出时清理临时目录会失败（Failed to remove temporary directory）。"""
        if not getattr(sys, "frozen", False):
            return   # 源码模式没有 _MEI 解包目录，无需处理
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/F", "/IM", "QtWebEngineProcess.exe"],
                    creationflags=0x08000000, capture_output=True, timeout=5)
        except Exception:
            pass

    atexit.register(_kill_child_processes)
    atexit.register(_cleanup_lock)
    try:
        app = DesktopPetApp()
        sys.exit(app.run())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        try:
            input("\n[Enter] 退出...")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
