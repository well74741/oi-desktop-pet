"""桌宠程序主入口"""

import sys
import os
import time
import datetime
import threading
import traceback


# 卡死诊断：设环境变量 OI_FAULTLOG=1 启动，每 10 秒把所有线程的 Python 栈写到
# %TEMP%/oi_pet_hang.log。必须放在模块最顶层——打包版连模块导入阶段都可能卡住，
# 放进 main() 里就抓不到了。打包版没控制台、异常又多被 except 吞掉，没有这个
# 手段查"卡死"基本靠猜。
if os.environ.get("OI_FAULTLOG"):
    try:
        import faulthandler as _faulthandler
        import tempfile as _tempfile
        _hang_fp = open(os.path.join(_tempfile.gettempdir(), "oi_pet_hang.log"),
                        "a", encoding="utf-8")
        _faulthandler.enable(file=_hang_fp)
        _faulthandler.dump_traceback_later(10, repeat=True, file=_hang_fp, exit=False)
    except Exception:
        pass


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


def _apply_app_font(app):
    """全局字体统一到微软雅黑。

    桌宠自己画的界面走 kit/QSS 指定字体，但托盘右键菜单、系统弹窗这些由 Qt
    直接建的控件用的是应用默认字体（Windows 上是 MS Shell Dlg 2 / 宋体一系），
    和桌宠正文对不上，看着就是"字体不统一"。这里把应用默认字体也换掉，
    上面那些控件不必逐个 setFont。
    （网页窗口是独立的浏览器进程，不受这里影响，也不该受影响。）
    """
    try:
        from PyQt5.QtGui import QFont
        f = QFont("Microsoft YaHei")
        # 装了非中文系统时雅黑可能缺席，留一条回退链
        f.setFamilies(["Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI",
                       "SimHei", "sans-serif"])
        f.setPointSizeF(14)
        app.setFont(f)
    except Exception:
        pass


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
        # 尺寸档位（设置窗口"桌宠外观"）：气泡大小 / 桌宠大小（含径向菜单），
        # 气泡用 kit.bs()、桌宠用 kit.ps() 内部缩放。
        # 全局缩放完全跟随 Windows 每块屏的实际缩放设置（不再强制 QT_SCALE_FACTOR）：
        # 强制 1.5 会让 100% 屏上的文字被位图放大而发糊，且多屏时 Qt 逻辑坐标跨屏
        # 不连续，导致径向菜单等窗口跳到别的屏幕。
        try:
            import pet_gravity as _pg
            _st = _pg.load_settings()
            from widgets import kit as _kit
            _kit.set_bubble_scale(float(_st.get("bubble_scale", 1.0) or 1.0))
            _kit.set_pet_scale(float(_st.get("pet_scale", 1.0) or 1.0))
        except Exception:
            pass
        # 任务管理器/托盘识别：统一应用身份与 oi.png 图标
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('oi桌宠')
            except Exception:
                pass
            # 开机自启自愈：开着自启但登记的路径不是现在这份程序（便携版换了
            # 目录、或装了新版本），悄悄改回来——否则开机启动的是旧版本甚至
            # 直接失败，而设置里还显示"已开启"
            try:
                import autostart as _autostart
                _autostart.refresh()
            except Exception:
                pass
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        # 125%/175% 这类非整数缩放按实际比例走，不四舍五入到 1x/2x
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass
        # AA_ShareOpenGLContexts 只为 QtWebEngine（聚合AI 内置网页窗）而设：
        # 轻量版没打包 QtWebEngine，不必设；find_spec 只查有没有，不会加载 Chromium。
        try:
            import importlib.util
            if importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is not None:
                QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        except Exception:
            pass
        self.app = QApplication(sys.argv)
        _apply_app_font(self.app)
        # 统一弹窗字体与按钮：QMessageBox/QInputDialog 默认 9pt 偏大、按钮无形状——
        # 用全局 QSS 统一到 11px 正文 + kit 规范按钮形状（边框/圆角/适中尺寸）。
        try:
            from widgets import kit
            _btn = kit.DIALOG_BTN_QSS
            self.app.setStyleSheet(
                # 悬停提示：全局一套，样式在 kit.TOOLTIP_QSS。挂在 QApplication 上
                # 所有窗口才都继承得到——气泡里的模块行以前没人给它样式，吃的是
                # 系统调色板那块黄底，同一个气泡能弹出两种长相的提示。
                kit.TOOLTIP_QSS +
                "QMessageBox{font-size:16px;} QMessageBox QLabel{font-size:16px;}"
                "QInputDialog{font-size:16px;} QInputDialog QLabel{font-size:16px;}"
                "QInputDialog QLineEdit,QInputDialog QPlainTextEdit{font-size:16px;}"
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
        # 应用默认字体（没指定字号的控件：托盘菜单/提示框/标准弹窗）
        try:
            from widgets import kit as _kz
            _kz.install_app_font(self.app)
        except Exception:
            pass

        self.config = load_config()
        self.pet = GravityPet(self.config)
        # 桌宠右键“退出”走与托盘退出一致的完整退出流程
        self.pet.quit_requested = self._quit

        self.tray_icon = None
        self._setup_tray()
        self.pet.show()
        # 在线更新：启动 30 秒后检查一次，之后每 6 小时一次（见 update_ui.py）
        try:
            import update_ui
            update_ui.start_scheduler(self.pet)
        except Exception:
            pass

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
        settings_action = QAction("设置", self.app)
        settings_action.setToolTip("打开桌宠设置（和右键桌宠 →「设置」是同一个窗口）")
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        show_action = QAction("显示桌宠", self.app)
        show_action.triggered.connect(self._show_pet)
        menu.addAction(show_action)

        hide_action = QAction("隐藏桌宠", self.app)
        hide_action.triggered.connect(self._hide_pet)
        menu.addAction(hide_action)

        center_action = QAction("回到中央", self.app)
        center_action.setToolTip("把桌宠移回当前屏幕中央（拖丢了用这个找回）")
        center_action.triggered.connect(self._center_pet)
        menu.addAction(center_action)

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

    def _open_settings(self):
        """托盘右键「设置」：桌宠是工具窗，不在任务栏里，托盘就是它唯一的常驻入口。
        桌宠被隐藏时也能改设置——先让它露面，再开同一个设置窗。"""
        try:
            if not self.pet.isVisible():
                self._show_pet()
            self.pet._open_settings()
        except Exception as e:
            try:
                from pet_gravity import _error_log
                _error_log("tray open settings failed: %r" % (e,))
            except Exception:
                pass

    def _hide_pet(self):
        self.pet.hide()

    def _center_pet(self):
        """托盘「回到中央」：把桌宠移回当前屏幕中央并显示（拖丢了用它找回）。"""
        try:
            self.pet.recenter()
        except Exception:
            self._show_pet()

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


# 安装程序用来判断"旧版还在跑"的具名互斥体。名字必须和 oi桌宠.iss 里的
# AppMutex 完全一致 —— 用纯 ASCII，免得编码在两边对不上。
APP_MUTEX_NAME = "oi_pet_desktop_single_instance"


def _create_app_mutex():
    """建一个具名互斥体，仅供安装程序检测"是否有实例在运行"。

    单实例判断仍然由 QLockFile 负责（跨平台、已经在用）；这里纯粹是为了让
    Inno Setup 的 AppMutex 能看见我们 —— 文件锁它看不见，于是以前装新版时
    不会提示"请先退出"，而是直接撞上 _internal 里 DLL 被占用，报
    "DeleteFile failed; code 5 拒绝访问"。

    返回句柄（失败返回 None）；进程退出时在 _cleanup_lock 里关掉。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
        return h or None
    except Exception:
        return None


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
    # 再额外建一个**具名互斥体**：QLockFile 是文件锁，安装程序看不见它。
    # Inno Setup 的 AppMutex 认的就是这种内核对象 —— 有了它，装新版时能直接
    # 提示"请先退出 oi桌宠"，而不是等替换 _internal 里的 DLL 时才报
    # "DeleteFile failed; code 5 拒绝访问"。名字必须和 .iss 里的 AppMutex 一致。
    _install_mutex = _create_app_mutex()

    def _cleanup_lock():
        try:
            lock.unlock()
        except Exception:
            pass
        try:
            if _install_mutex:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(_install_mutex)
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

    def _cleanup_reminders():
        """退出前取消所有进程内提醒定时器。"""
        try:
            from status_monitor import cancel_all_reminders
            cancel_all_reminders()
        except Exception:
            pass

    def _cleanup_temp_images():
        """清理聊天/粘贴产生的临时图片（oi_chat_* / oi_paste_*），避免 %TEMP% 累积。"""
        try:
            import glob as _glob
            tmpd = tempfile.gettempdir()
            for pat in ("oi_chat_*", "oi_paste_*"):
                for f in _glob.glob(os.path.join(tmpd, pat)):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        except Exception:
            pass

    atexit.register(_cleanup_temp_images)
    atexit.register(_cleanup_reminders)
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
