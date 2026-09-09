"""桌宠核心 - 桌面移动、边缘吸附、呼吸动画、径向快捷启动"""

import os
import sys
import re
import math
import json
import logging
import random
import subprocess
import threading
import webbrowser
import time
import weakref
from collections import OrderedDict

from PyQt5.QtWidgets import (
    QWidget, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QGroupBox, QFileDialog,
    QInputDialog, QLabel, QCheckBox, QLineEdit,
    QSpinBox, QComboBox, QPlainTextEdit,
    QMessageBox, QLayout, QMenu, QSlider
)
from PyQt5.QtCore import Qt, QObject, QTimer, QPoint, QElapsedTimer, QPointF, QRect, QRectF, QSize, QEvent, pyqtSignal
from PyQt5.QtGui import (QPixmap, QPainter, QColor, QPen, QFont, QFontMetrics, QIcon, QCursor,
                         QDrag, QPainterPath, QRadialGradient, QImage, QMovie,
                         QSyntaxHighlighter, QTextCharFormat)
from bubble_ui import StatusBubble
from status_monitor import MoodBubble
from bubble_layout import StatusBubbleLayout
from h5_cards import ResultView
from widgets import kit as _kit   # 径向菜单/桌宠尺寸档位缩放共用
from module_core import APP_VERSION

logger = logging.getLogger(__name__)


def _asset_icon(name):
    """加载打包兼容的功能窗口图标；缺失时返回空图标，不让 UI 初始化失败。"""
    if getattr(sys, "frozen", False):
        p = os.path.join(sys._MEIPASS, "assets", name)
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", name)
    return QIcon(p) if os.path.exists(p) else QIcon()


def _elide_text(s, width=250):
    """文字过长用省略号代替"""
    try:
        fm = QFontMetrics(QFont("SimHei", 9))
        return fm.elidedText(s, Qt.ElideRight, width)
    except Exception:
        return s


# ==================== Windows API ====================
_CAPTURE_TOOLS = {
    "snippingtool.exe", "screensketch.exe", "screenclippinghost.exe",
    "snipaste.exe", "sharex.exe", "picpick.exe", "greenshot.exe",
    "fscapture.exe", "wechat.exe", "weixin.exe", "qq.exe",
    "dingtalk.exe", "feishu.exe", "obs64.exe", "obs32.exe",
    "pixpin.exe", "lightshot.exe", "prtscr.exe", "gamebar.exe",
    "gamebarpresencewriter.exe", "wps.exe", "wpp.exe", "360screenshot.exe",
    "sogouinput.exe", "tencent_screenshot.exe", "clipdiary.exe",
}
# 截图/录屏工具的关键字匹配（进程名包含即视为截图工具，覆盖更多第三方工具）
_CAPTURE_KEYWORDS = (
    "snip", "screenshot", "screenclip", "capture", "pixpin", "sharex",
    "greenshot", "picpick", "fscapture", "snipaste", "lightshot",
    "gamebar", "bandicam", "ocam", "hypercam", "clipdiary", "prtscr",
)


def _is_capture_tool(name):
    """判断前台进程是否为截图/录屏工具（精确名或关键字匹配）。"""
    if not name:
        return False
    if name in _CAPTURE_TOOLS:
        return True
    return any(k in name for k in _CAPTURE_KEYWORDS)


def _foreground_process_name():
    """返回前台窗口所属进程名（小写），失败返回空串。"""
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if hwnd == 0:
            return ""
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def is_foreground_fullscreen():
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd == 0:
            return False
        # 排除桌面、任务栏等 shell 窗口：点击桌面/任务栏预览时不应被判为全屏
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        # 截图/录屏工具的全屏覆盖层不应触发隐藏桌宠
        if _is_capture_tool(_foreground_process_name()):
            return False
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        style = user32.GetWindowLongW(hwnd, -16)
        ww, wh = rect.right - rect.left, rect.bottom - rect.top
        if ww < sw or wh < sh:
            return False
        # 必须是真正带标题栏的应用窗口，避免把全屏覆盖的 shell 窗口误判
        if not (style & 0x00C00000):
            return True
        if rect.left <= 0 and rect.top <= 0:
            return True
        return False
    except Exception:
        return False


# ==================== 文件图标提取（带缓存）====================
_icon_cache = OrderedDict()
_lnk_target_cache = {}  # .lnk 目标解析缓存，避免反复启动 PowerShell
_shell_iid = None  # SHGetImageList 的 IID 缓冲区（只初始化一次）


def extract_exe_icon(path: str, size: int = 32) -> QPixmap:
    cache_key = (path, size)
    if cache_key in _icon_cache:
        _icon_cache.move_to_end(cache_key)
        return _icon_cache[cache_key]
    pm = _extract_icon_impl(path, size)
    _icon_cache[cache_key] = pm
    if len(_icon_cache) > 200:
        _icon_cache.popitem(last=False)
    return pm


def _resolve_lnk_target(lnk_path: str) -> str:
    """解析 .lnk 快捷方式的目标路径（带缓存，避免反复启动 PowerShell）"""
    if sys.platform != 'win32':
        return ''
    cached = _lnk_target_cache.get(lnk_path)
    if cached is not None:
        return cached
    target = ''
    try:
        import subprocess
        ps = (
            f"$s=New-Object -ComObject WScript.Shell;"
            f"$l=$s.CreateShortcut('{lnk_path.replace(chr(39), chr(39)+chr(39))}');"
            f"$l.TargetPath"
        )
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000
        )
        target = r.stdout.strip()
    except Exception:
        target = ''
    _lnk_target_cache[lnk_path] = target
    return target


def _prewarm_lnk_cache(paths):
    """一次性批量解析未缓存的 .lnk 目标（后台线程，避免展开菜单卡顿）"""
    pending = []
    for p in paths:
        if p and p.lower().endswith('.lnk') and p not in _lnk_target_cache:
            pending.append(p)
    if not pending:
        return

    def work():
        try:
            parts = []
            for i, p in enumerate(pending):
                safe = p.replace(chr(39), chr(39) + chr(39))
                parts.append(
                    f"$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut('{safe}');"
                    f"Write-Output ('###{i}###' + $l.TargetPath)"
                )
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ';'.join(parts)],
                capture_output=True, text=True, timeout=15, creationflags=0x08000000
            )
            for line in r.stdout.splitlines():
                seg = line.split('###')
                if len(seg) >= 3 and seg[1].isdigit():
                    idx = int(seg[1])
                    if 0 <= idx < len(pending):
                        _lnk_target_cache[pending[idx]] = seg[2].strip()
        except Exception:
            pass
        for p in pending:
            _lnk_target_cache.setdefault(p, '')

    threading.Thread(target=work, daemon=True).start()


def _extract_icon_impl(path: str, size: int) -> QPixmap:
    if not path or not os.path.exists(path):
        return QPixmap()
    # .lnk 快捷方式：解析目标 exe，从目标取高分辨率图标
    target = path
    if path.lower().endswith('.lnk'):
        resolved = _resolve_lnk_target(path)
        if resolved and os.path.exists(resolved):
            target = resolved
    pm = _get_shell_icon(target, size)
    if pm and not pm.isNull():
        return pm
    return _get_fallback_icon(path, size)


def _get_shell_icon(path: str, size: int) -> QPixmap:
    """通过 Windows API 取系统图标，优先取最高分辨率"""
    if sys.platform != 'win32':
        return QPixmap()
    try:
        import ctypes
        import ctypes.wintypes
        from ctypes import windll, Structure, c_int, byref, c_void_p
        from ctypes.wintypes import DWORD, HANDLE, MAX_PATH

        SHGFI_SYSICONINDEX = 0x000004000
        SHGFI_USEFILEATTRIBUTES = 0x000000010
        FILE_ATTRIBUTE_NORMAL = 0x80
        FILE_ATTRIBUTE_DIRECTORY = 0x10

        class SHFILEINFO(Structure):
            _fields_ = [
                ("hIcon", HANDLE),
                ("iIcon", c_int),
                ("dwAttributes", DWORD),
                ("szDisplayName", ctypes.c_wchar * MAX_PATH),
                ("szTypeName", ctypes.c_wchar * 80),
            ]

        shell32 = windll.shell32
        ole32 = windll.ole32

        # 判断是文件还是目录
        is_dir = os.path.isdir(path)
        attr = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_NORMAL

        info = SHFILEINFO()
        ret = shell32.SHGetFileInfoW(
            path, attr, byref(info),
            ctypes.sizeof(info), SHGFI_SYSICONINDEX | SHGFI_USEFILEATTRIBUTES
        )
        if not ret or info.iIcon < 0:
            return QPixmap()

        # 取系统图像列表：依次尝试 JUMBO(256) → EXTRALARGE(48) → LARGE(32)
        global _shell_iid
        if _shell_iid is None:
            IID_buf = (ctypes.c_byte * 16)()
            ole32.IIDFromString('{46EB5926-582E-4017-9FDF-E8998DAA0950}', IID_buf)
            _shell_iid = IID_buf
        for il_type in (0x3, 0x2, 0x1):
            himl = c_void_p()
            hr = shell32.SHGetImageList(il_type, _shell_iid, byref(himl))
            if hr >= 0 and himl.value:
                cx, cy = c_int(), c_int()
                windll.comctl32.ImageList_GetIconSize(himl, byref(cx), byref(cy))
                if cx.value >= 32:
                    hicon = windll.comctl32.ImageList_GetIcon(himl, info.iIcon, 1)
                    if hicon:
                        from PyQt5.QtWinExtras import QtWin
                        pm = QtWin.fromHICON(hicon)
                        windll.user32.DestroyIcon(hicon)
                        if pm and not pm.isNull():
                            return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QPixmap()
    except Exception:
        return QPixmap()


def _get_fallback_icon(path: str, size: int) -> QPixmap:
    """QFileIconProvider 降级方案"""
    try:
        from PyQt5.QtWidgets import QFileIconProvider
        from PyQt5.QtCore import QFileInfo
        provider = QFileIconProvider()
        icon = provider.icon(QFileInfo(path))
        if icon.isNull():
            return QPixmap()
        pm = icon.pixmap(size, size)
        return pm if not pm.isNull() else QPixmap()
    except Exception:
        return QPixmap()


# ==================== 缓动函数 ====================
def ease_out_back(t):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


def ease_out_cubic(t):
    return 1.0 - pow(1.0 - t, 3)


def smoothstep(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def _mid_angle(a, b):
    """两个角度的圆周中点（走较短路径），避免跨 0°/2π 出错"""
    diff = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + diff / 2.0


def ease_out_back_strong(t):
    c1, c3 = 2.8, 3.8
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


# ==================== 常量 ====================
EDGE_SNAP_THRESHOLD = 5  # 吸附到屏幕边缘的判定距离（再小一半）
EDGE_TILT_ANGLE = 25
EDGE_SHOW_RATIO = 0.5
BREATH_SCALE = 0.06
PRESS_SCALE = 0.88                # 按下时的"按下去"目标缩放（缩小）
PRESS_DOWN_DURATION = 0.03         # 按下过渡时长（秒，快速）
RELEASE_DURATION = 0.06            # 松手回弹时长（与按压等速，互为反向动画）
BREATH_PERIOD = 1.667              # 呼吸周期（秒，加快三分之一）
ELASTIC_DURATION = 0.22
ELASTIC_AMPLITUDE = 0.18
ELASTIC_CYCLES = 1.5
ELASTIC_DECAY = 1.2
DISK_ANIM_DELAY = 0.05  # 背景盘展开/收回比首/末按钮慢 0.05s（提前 50%）
PET_SHADOW_MARGIN = 8   # 桌宠窗口四周留白，容纳阴影与放大过冲
MENU_QSS = """
    QMenu{background:#232a3a;border:1px solid #4a5468;border-radius:6px;padding:2px;
           font-family:"SimHei";font-size:11px;color:#d5dbe8}
    QMenu::item{padding:4px 14px;border-radius:3px;margin:0 1px}
    QMenu::item:selected{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4a90e2,stop:1 #2fb8c0);color:#ffffff}
    QMenu::separator{height:1px;background:#39414f;margin:2px 6px}
"""

class StyledMenu(QWidget):
    """自定义圆角弹出菜单，用 QPainter 画圆角白色背景板，无原生黑色边框。"""
    _qss_item = (
        "QPushButton{text-align:left;padding:6px 18px;border:none;background:transparent;"
        "font-family:'SimHei';font-size:11px;color:#d5dbe8;border-radius:6px}"
        "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #4a90e2,stop:1 #2fb8c0);color:#ffffff}"
        "QPushButton:pressed{background:#3a80d0;color:#ffffff}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._result = None
        self._loop = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        self._btn_layout = layout

    def addAction(self, text):
        btn = QPushButton(text, self)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self._qss_item)
        btn.clicked.connect(lambda _=False, t=text: self._on_click(t))
        self._btn_layout.addWidget(btn)
        return text

    def addSeparator(self):
        sep = QWidget(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#39414f;margin:2px 6px")
        self._btn_layout.addWidget(sep)

    def _on_click(self, text):
        self._result = text
        self.close()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(QColor(35, 42, 58))
        p.setPen(QPen(QColor(90, 100, 120), 1))
        p.drawRoundedRect(rect, 8, 8)
        p.end()

    def exec_(self, pos=None):
        if pos is not None:
            self.move(pos)
        self.adjustSize()
        self._result = None
        self.show()
        self.raise_()
        self.activateWindow()
        from PyQt5.QtCore import QEventLoop
        self._loop = QEventLoop()
        self._loop.exec_()
        self._loop = None
        return self._result

    def closeEvent(self, event):
        if self._loop is not None:
            self._loop.quit()
        super().closeEvent(event)


CONFIG_FILE = "pet_settings.json"


# 默认桌宠图标：内置动图 yxm.webp（默认外观）；"恢复默认"在动图 yxm.webp
# 与静态图 oi.png 之间循环切换，点击一下换一个。
_DEFAULT_PET_IMAGES = ["assets/oi.png", "assets/yxm.webp"]

# 尺寸档位（标准=最低档 1.0）：
#   bubble_scale 气泡大小 → kit.bs() 缩放气泡/组件内部；
#   pet_scale    桌宠大小（含径向菜单）→ kit.ps() 缩放桌宠本体与菜单按钮。
# 全局 QT_SCALE_FACTOR 固定 1.5 作为低缩放屏基准（main.py 设置）。
UI_SCALE_OPTIONS = [("标准", 1.0), ("较大", 1.25), ("大", 1.5), ("特大", 2.0)]


def _same_path(a, b):
    """比较两个路径是否指向同一文件（相对/绝对、大小写无关）。"""
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return a == b


def _default_pet_image_abs(p):
    """默认图标（相对路径）解析为项目目录下的绝对路径。"""
    if not p or os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), p)


def _resolve_image_candidates(image_path):
    """返回按优先级排列的图标候选路径。

    打包（PyInstaller 单文件）后资源在解包临时目录（sys._MEIPASS）里，
    不能按运行目录（exe 所在目录）解析相对路径，必须先按 __file__ 解析默认图标。
    """
    cands = [str(image_path or "")]
    if image_path in _DEFAULT_PET_IMAGES:
        cands.insert(0, _default_pet_image_abs(image_path))
    cands += ["assets/yxm.webp", "assets/oi.png", "oi.png",
              _default_pet_image_abs("assets/yxm.webp")]
    return [c for c in cands if c]


def _next_default_pet_image(cur):
    """恢复默认：在 oi.png / yxm.webp 两个默认图标间循环。"""
    for i, d in enumerate(_DEFAULT_PET_IMAGES):
        if _same_path(cur, d) or _same_path(cur, _default_pet_image_abs(d)):
            return _DEFAULT_PET_IMAGES[(i + 1) % len(_DEFAULT_PET_IMAGES)]
    return _DEFAULT_PET_IMAGES[0]


# ==================== 预置模块（桌宠自带，默认在列表中，与普通模块一样可编辑/删除）====================
_BUILTIN_RULES = [
    {"id": "builtin_cpu", "name": "CPU", "builtin": "cpu", "interval": 2,
     "enabled": True,
     "source": {"type": "script", "lang": "python",
                "code": "if 'p' not in state:\n"
                        "    from status_monitor import CpuProvider\n"
                        "    state['p'] = CpuProvider()\n"
                        "result = state['p'].collect()"}},
    {"id": "builtin_memory", "name": "内存", "builtin": "memory", "interval": 2,
     "enabled": True,
     "source": {"type": "script", "lang": "python",
                "code": "from status_monitor import MemoryProvider\n"
                        "result = MemoryProvider().collect()"}},
    {"id": "builtin_battery", "name": "电池", "builtin": "battery", "interval": 30,
     "enabled": True,
     "source": {"type": "script", "lang": "python",
                "code": "from status_monitor import BatteryProvider\n"
                        "result = BatteryProvider().collect()"}},
    {"id": "builtin_mood", "name": "情绪", "builtin": "mood", "interval": 600,
     "enabled": True, "popup": True, "embed": False,
     "source": {"type": "script", "lang": "python",
                "code": "import random\n"
                        "result = random.choice([\"加油！代码会变好的～\", \"写得不错嘛！\", "
                        "\"累了就喝口水吧 ☕\", \"稳住，能赢！\", \"别急，答案正在路上\"])"}},
    {"id": "builtin_webchat", "name": "聚合AI", "builtin": "webchat",
     "interval": 3600, "enabled": True, "chat": True,
     "source": {"type": "script", "lang": "python", "ui": "webchat",
                "url": "https://chat.deepseek.com",
                "code": "result = '聚合AI'", "timeout": 5}},
    {"id": "builtin_canvas", "name": "无限画布", "builtin": "canvas",
     "interval": 3600, "enabled": True,
     "source": {"type": "script", "lang": "python", "ui": "canvas",
                "code": "result = ''", "timeout": 5}},
    {"id": "builtin_weather", "name": "天气", "builtin": "weather",
     "interval": 3600, "enabled": True,
     "source": {"type": "http", "url": "https://wttr.in/?format=%c+%t",
                "timeout": 5, "plain_text": True},
     "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "$1"},
     "fallback": "天气获取失败"},
    {"id": "builtin_pomodoro", "name": "番茄钟", "builtin": "pomodoro",
     "interval": 30, "enabled": False,
     "source": {"type": "script", "lang": "python", "ui": "pomodoro",
                "code": "result = ''", "timeout": 5}},
    {"id": "builtin_qqq", "name": "QQQ", "builtin": "qqq",
     "interval": 30, "enabled": True, "popup": True, "embed": False,
     "source": {"type": "script", "lang": "python",
                "code": ("import random\n"
                         "result = random.choice([\"加油！代码会变好的～\", \"写得不错嘛！\", "
                         "\"累了就喝口水吧 ☕\", \"稳住，能赢！\", \"别急，答案正在路上\"])")}},
    {"id": "builtin_perler", "name": "拼豆", "builtin": "perler",
     "interval": 3600, "enabled": True,
     "source": {"type": "script", "lang": "python", "ui": "perler",
                "code": "result = ''", "timeout": 5}},
    {"id": "builtin_tokenmeter", "name": "Token 消耗", "builtin": "tokenmeter",
     "interval": 60, "enabled": False,
     "source": {"type": "script", "lang": "python", "ui": "tokenmeter",
                "code": "result = ''", "timeout": 5}},
    {"id": "builtin_ai", "name": "AI助手", "builtin": "ai",
     "interval": 60, "enabled": False, "chat": True,
     "source": {"type": "llm", "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat", "api_key": "<你的API Key>",
                "system_prompt":
                    "你是桌宠的 AI 助手。你拥有这些能力（看到相关需求必须调用"
                    "对应工具来真正执行，禁止只口头说'已设置/已删除'而不调用"
                    "工具）：查时间/开网页/设置定时提醒；管理待办（添加、查看、"
                    "删除）；管理气泡模块（列出、新建、删除、启停、排序）；管理"
                    "径向菜单按钮（列出、添加、删除、排序、编辑）；控制桌宠本体"
                    "（隐藏/显示/移动）；调整桌宠设置（大小/透明度等，先 pet_info"
                    "查看当前值再 pet_setting 修改）；画画（用户说'画布/无限画布/绘图'"
                    "用 canvas_draw，说'拼豆/像素画/像素点'用 perler_draw，"
                    "两者都用中文描述要画的内容）。用户需要提取/整理为固定"
                    "格式（如 JSON、列表）时，直接输出对应结构，不要附加解释。"
                    "其余问题直接简短回答。",
                "user_prompt": "你好", "temperature": 0.8, "max_tokens": 8192,
                "tools": ["get_time", "add_todo", "list_todos", "open_url",
                          "remind", "delete_todo", "list_modules",
                          "add_module", "remove_module", "enable_module",
                          "move_module", "list_buttons", "add_button",
                          "remove_button", "move_button", "edit_button",
                          "pet_control", "pet_setting", "pet_info",
                          "list_components", "list_templates",
                          "add_module_from_template", "canvas_draw", "perler_draw"]},
     "greeting": "你好！我是你的 AI 助手，可以：管理待办/模块/按钮、"
                 "控制桌宠、调整桌宠设置、查时间、开网页、设提醒。",
     "fallback": "AI 不可用"},
]


# ==================== 配置持久化 ====================
def get_config_path():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_FILE)


def load_settings() -> dict:
    path = get_config_path()
    defaults = {"pet_image": "", "pet_size": 75, "pet_opacity": 1.0, "button_size": 25,
                "slot_shortcuts": [], "show_tooltips": True, "status_enabled": {},
                "status_custom_items": [], "status_rules": [], "hidden_builtins": [],
                "bubble_scale": 1.0, "pet_scale": 1.0}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                defaults.update({k: v for k, v in saved.items() if k in defaults})
        except Exception:
            pass
    # 插槽最多保留 8 个：防止旧数据中隐藏的第 9+ 项在删除按钮时"补位"
    defaults["slot_shortcuts"] = defaults.get("slot_shortcuts", [])[:8]
    # 内置规则合并进规则列表（缺失时补入；已存在则保持用户排序）
    rules = list(defaults.get("status_rules", []))
    hidden = set(defaults.get("hidden_builtins") or [])   # 用户删除过的内置模块不再自动加回
    existing_ids = {r.get("id") for r in rules}
    # 已存在的同名内置（旧规则打过 builtin 标记）不再重复补入新内置
    existing_builtins = {r.get("builtin") for r in rules if r.get("builtin")}
    new_builtin_ids = [b["id"] for b in _BUILTIN_RULES
                       if b["id"] not in existing_ids
                       and b["id"] not in hidden
                       and b.get("builtin") not in existing_builtins]
    merged = ([dict(b) for b in _BUILTIN_RULES
               if b["id"] not in existing_ids
               and b["id"] not in hidden
               and b.get("builtin") not in existing_builtins] + rules)
    st_en = defaults.get("status_enabled") or {}
    mood_on = defaults.get("mood_enabled", True)
    for r in merged:
        # 只迁移"新加入"的内置规则；已保存的内置规则保持用户勾选状态
        tpl = {b["id"]: b for b in _BUILTIN_RULES}.get(r.get("id"))
        if tpl:
            # 内置模块的弹出属性等选项始终同步模板（如情绪=自动弹出）
            r.setdefault("popup", tpl.get("popup", False))
            # 旧版"聚合网页AI"改名为"聚合AI"：标题过长，气泡行内显示不全
            if (r.get("id") == "builtin_webchat"
                    and r.get("name") == "聚合网页AI"):
                r["name"] = "聚合AI"
            bk = r.get("builtin")
            if r.get("id") in new_builtin_ids:
                if bk in ("cpu", "memory", "battery", "network") and bk in st_en:
                    r["enabled"] = bool(st_en[bk])
                if bk == "mood":
                    r["enabled"] = bool(mood_on)
    # 嵌入/自动弹出互斥归一化：情绪保留弹出，其余（含对话类）保留嵌入
    for r in merged:
        embed = bool(r.get("embed", True))
        popup = bool(r.get("popup", False))
        if embed and popup:
            if r.get("builtin") == "mood":
                r["embed"] = False
            else:
                r["popup"] = False
    # llm 规则自动补充 AI 绘画工具（仅当用户明确要求绘制时模型才会调用）：
    # canvas_draw=无限画布，perler_draw=拼豆像素画
    _DRAW_CAP = ("；画画（用户说'画布/无限画布/绘图'用 canvas_draw，"
                 "说'拼豆/像素画/像素点'用 perler_draw，两者都用中文描述要画的内容）")
    for r in merged:
        src = r.get("source") or {}
        if isinstance(src, dict) and src.get("type") == "llm":
            tools = src.get("tools")
            if isinstance(tools, list):
                for _t in ("canvas_draw", "perler_draw"):
                    if _t not in tools:
                        tools.append(_t)
            # 旧 AI 规则的 system_prompt 可能未提绘画能力：补上，保证模型会调用绘画工具
            sp = str(src.get("system_prompt", "") or "")
            if "canvas_draw" not in sp and "画画" not in sp:
                src["system_prompt"] = sp.rstrip() + _DRAW_CAP
    # http 纯文本接口迁移：wttr.in 用浏览器 UA 会返回完整网页，需 curl UA
    for r in merged:
        src = r.get("source") or {}
        if isinstance(src, dict) and src.get("type") == "http":
            url = str(src.get("url", ""))
            if "wttr.in" in url:
                src.setdefault("plain_text", True)
    # 旧自定义"天气/QQQ"升级为内置：打上 builtin 标记，避免与新内置重复两份
    for r in merged:
        name = str(r.get("name", ""))
        src = r.get("source") or {}
        if r.get("builtin"):
            continue
        if name == "天气" and (src.get("type") == "http"
                              and "wttr.in" in str(src.get("url", ""))):
            r["builtin"] = "weather"
        elif name == "QQQ" and src.get("type") == "script":
            r["builtin"] = "qqq"
    # 内置去重：同一 builtin 只保留一条（保留用户配置的旧规则，删模板补入的重复）
    seen_b = {}
    deduped = []
    for r in merged:
        b = r.get("builtin")
        if b and b in seen_b:
            continue   # 跳过重复内置（模板补入的）
        if b:
            seen_b[b] = r
        deduped.append(r)
    merged = deduped
    # 同名去重：内置与自定义不能同名。同名时保留"更该保留"的：
    # 启用的 > 内置标记的 > 先出现的（默认内置模板）。避免把用户配置的真实 AI 挤掉。
    def _same_name_keep(a, b):
        a_on, b_on = bool(a.get("enabled")), bool(b.get("enabled"))
        if a_on != b_on:
            return a if a_on else b
        a_b, b_b = bool(a.get("builtin")), bool(b.get("builtin"))
        if a_b != b_b:
            return a if a_b else b
        return a
    seen_name = {}
    name_dedup = []
    for r in merged:
        nm = str(r.get("name", "") or "").strip()
        if not nm:
            name_dedup.append(r)
            continue
        if nm in seen_name:
            keep = _same_name_keep(seen_name[nm], r)
            if keep is r:
                idx = next(i for i, x in enumerate(name_dedup) if x is seen_name[nm])
                name_dedup[idx] = r
                seen_name[nm] = r
            continue
        seen_name[nm] = r
        name_dedup.append(r)
    merged = name_dedup
    # 移除不再需要的"网络"内置（用户已删或新装不再提供）
    merged = [r for r in merged if r.get("builtin") != "network"]
    if not any(r.get("builtin") == "network" for r in merged):
        defaults.setdefault("hidden_builtins", []).append("builtin_network")
    defaults["status_rules"] = merged
    # 旧版静态自定义项迁移为 static 规则（向后兼容）
    if not defaults.get("status_rules") and defaults.get("status_custom_items"):
        defaults["status_rules"] = [{
            "id": "r%d_%d" % (int(time.time()), i),
            "name": it.get("name", "自定义"),
            "interval": 60,
            "enabled": True,
            "source": {"type": "static", "text": it.get("value", "")},
        } for i, it in enumerate(defaults["status_custom_items"])]
        defaults["status_custom_items"] = []
    return defaults


def save_settings(settings: dict):
    try:
        with open(get_config_path(), 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to save settings")


def _diag_log(msg):
    """诊断日志：设置窗口打开链路（轻量常驻，用于排查问题）"""
    try:
        with open(os.path.join(os.environ.get("TEMP", os.environ.get("TMP", ".")),
                               "oi_pet_diag.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _set_dark_titlebar(win):
    """把窗口标题栏设为深色（Windows 10/11），与护眼暗色主题统一。"""
    try:
        if sys.platform != "win32":
            return
        hwnd = int(win.winId())
        import ctypes
        val = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE（Win11=20 / Win10=19）
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
                break
            except Exception:
                continue
    except Exception:
        pass


def _error_log(msg):
    """错误日志：写入 %TEMP%/oi_pet_error.log，方便排查闪退/异常。"""
    try:
        with open(os.path.join(os.environ.get("TEMP", os.environ.get("TMP", ".")),
                               "oi_pet_error.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ==================== 名称提示标签 ====================
class _TipLabel(QWidget):
    _font = QFont("SimHei", 8)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._text = ""
        # Windows 系统级鼠标穿透：标签完全不参与鼠标判定
        if sys.platform == 'win32':
            try:
                import ctypes
                hwnd = int(self.winId())
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x20)  # WS_EX_TRANSPARENT
            except Exception:
                pass

    def text(self):
        return self._text

    def setText(self, text):
        if text != self._text:
            self._text = text
            try:
                fm = QFontMetrics(self._font)
                self.resize(fm.width(text) + 10, fm.height() + 6)
                self.setFixedSize(fm.width(text) + 10, fm.height() + 6)
            except Exception:
                self.resize(80, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(255, 255, 255, 180))
        p.setPen(QPen(QColor(187, 187, 187, 150), 1))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 6, 6)
        p.setPen(QColor(51, 51, 51))
        p.setFont(self._font)
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()


class _WebpAnim(QObject):
    """Pillow 解码的 webp 动图播放器（静态 webp 也兼容）"""

    def __init__(self, path, target_size, on_frame, parent=None):
        super().__init__(parent)
        self._target_size = target_size
        self._on_frame = on_frame
        self._frames = []
        self._durations = []
        self._idx = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._next)
        self._load(path)

    def _load(self, path):
        try:
            from PIL import Image, ImageSequence
            im = Image.open(path)
            for frame in ImageSequence.Iterator(im):
                frame = frame.convert("RGBA")
                data = frame.tobytes("raw", "RGBA")
                qimg = QImage(data, frame.width, frame.height, QImage.Format_RGBA8888).copy()
                # 优先用每帧时长，保证播放速度与原图一致
                d = frame.info.get("duration") or im.info.get("duration") or 80
                self._frames.append(qimg)
                self._durations.append(max(20, int(d)))
        except Exception:
            self._frames = []
            self._durations = []

    def is_valid(self):
        return len(self._frames) > 0

    def start(self):
        if not self._frames:
            return
        if len(self._frames) > 1:
            self._timer.start(max(30, self._durations[0]))
        if self._on_frame:
            self._on_frame()

    def stop(self):
        self._timer.stop()

    def current_pixmap(self):
        if not self._frames:
            return QPixmap()
        pm = QPixmap.fromImage(self._frames[min(self._idx, len(self._frames) - 1)])
        return pm.scaled(self._target_size, self._target_size,
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _next(self):
        if not self._frames:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        self._timer.start(max(30, self._durations[self._idx]))
        if self._on_frame:
            self._on_frame()


class ConfirmPopup(QWidget):
    """拖入添加/替换的确认弹窗：出现在鼠标附近，风格与菜单统一。"""
    _qss = (
        "QWidget{background:#232a3a;border:1px solid #4a5468;border-radius:8px}"
        "QLabel{color:#d5dbe8;font-family:'SimHei';font-size:11px}"
        "QPushButton{font-family:'SimHei';font-size:11px;color:#ffffff;"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4aa3ff,stop:1 #2fb8c0);"
        "border:none;border-radius:5px;padding:5px 14px;min-width:56px}"
        "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #5ab3ff,stop:1 #3fc8d0)}"
        "QPushButton#cancel{color:#aab3c5;background:#2b3446;border:1px solid #465066}"
        "QPushButton#cancel:hover{background:#3a4a63;color:#ffffff}"
    )

    def __init__(self, text):
        super().__init__(None)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(self._qss)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        lay.addWidget(self._label)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok = QPushButton("确定")
        self._cancel = QPushButton("取消")
        self._cancel.setObjectName("cancel")
        btn_row.addWidget(self._ok)
        btn_row.addWidget(self._cancel)
        lay.addLayout(btn_row)
        self._result = False
        self._loop = None
        self._ok.clicked.connect(lambda: self._finish(True))
        self._cancel.clicked.connect(lambda: self._finish(False))

    def _finish(self, result):
        self._result = result
        self.close()

    def closeEvent(self, event):
        if self._loop is not None and self._loop.isRunning():
            self._loop.quit()
        super().closeEvent(event)

    def exec_(self, pos):
        self.adjustSize()
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = min(pos.x(), g.right() - self.width() - 4)
        y = min(pos.y() + 14, g.bottom() - self.height() - 4)
        self.move(max(g.left() + 4, x), max(g.top() + 4, y))
        self._result = False
        self.show()
        self.raise_()
        self.activateWindow()
        from PyQt5.QtCore import QEventLoop
        self._loop = QEventLoop()
        self._loop.exec_()
        self._loop = None
        return self._result


def _hostname_of(url):
    """从网址提取默认按钮名（如 https://www.deepseek.com/ → DeepSeek）。"""
    try:
        from urllib.parse import urlparse
        h = (urlparse(str(url)).hostname or "").replace("www.", "")
        return h.capitalize() or "网页"
    except Exception:
        return "网页"


class _AddTextDialog(QDialog):
    """通用"添加按钮"文本弹窗：名称 + 值，深色样式，出现在鼠标附近。
    子类重写 validate(value) / auto_name(value) 即可复用。"""

    def __init__(self, title, name_ph, value_ph, pos=None, name="", value="",
                 parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(280)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        t = QLabel(title)
        t.setStyleSheet(
            "color:#e8ecf5;font-family:'SimHei';font-size:12px;font-weight:600;"
            "background:transparent;")
        lay.addWidget(t)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText(name_ph)
        self.name_edit.setStyleSheet(self._edit_qss())
        lay.addWidget(self.name_edit)

        self.value_edit = QLineEdit(value)
        self.value_edit.setPlaceholderText(value_ph)
        self.value_edit.setStyleSheet(self._edit_qss())
        lay.addWidget(self.value_edit)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#e06c6c;font-family:'SimHei';font-size:10px;"
                                "background:transparent;")
        self.hint.setVisible(False)
        lay.addWidget(self.hint)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setStyleSheet(self._btn_qss(False))
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setStyleSheet(self._btn_qss(True))
        ok.clicked.connect(self._ok)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self.value_edit.returnPressed.connect(self._ok)
        self.name_edit.returnPressed.connect(lambda: self.value_edit.setFocus())
        self._name = ""
        self._value = ""
        if pos is not None:
            self.adjustSize()
            g = QApplication.primaryScreen().availableGeometry()
            x = max(g.left() + 4, min(pos.x() - self.width() // 2,
                                      g.right() - self.width() - 4))
            y = max(g.top() + 4, min(pos.y() - 8, g.bottom() - self.height() - 4))
            self.move(x, y)

    @staticmethod
    def _edit_qss():
        return ("QLineEdit{background:#1c2030;border:1px solid #465066;"
                "border-radius:5px;color:#e8ecf5;font-family:'SimHei';"
                "font-size:11px;padding:5px 7px;}"
                "QLineEdit:focus{border-color:#4a90e2;}")

    @staticmethod
    def _btn_qss(primary):
        if primary:
            return ("QPushButton{font-family:'SimHei';font-size:11px;color:#ffffff;"
                    "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                    "stop:0 #4aa3ff,stop:1 #2fb8c0);border:none;border-radius:5px;"
                    "padding:5px 14px;min-width:56px}"
                    "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                    "stop:0 #5ab3ff,stop:1 #3fc8d0)}")
        return ("QPushButton{font-family:'SimHei';font-size:11px;color:#aab3c5;"
                "background:#2b3446;border:1px solid #465066;border-radius:5px;"
                "padding:5px 14px;min-width:56px}"
                "QPushButton:hover{background:#3a4a63;color:#ffffff}")

    def validate(self, value):
        """子类重写：返回 (是否通过, 错误提示)。"""
        return (bool(value.strip()), "" if value.strip() else "内容不能为空")

    def auto_name(self, value):
        """子类重写：名称留空时根据值生成默认名称。"""
        return ""

    def _ok(self):
        value = self.value_edit.text().strip()
        ok, err = self.validate(value)
        if not ok:
            self.hint.setText(err or "内容不合法")
            self.hint.setVisible(True)
            return
        name = self.name_edit.text().strip()
        self._name = name or self.auto_name(value)
        self._value = value
        self.accept()

    def result_pair(self):
        return (self._name, self._value)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(35, 42, 58))
        p.setPen(QPen(QColor(74, 84, 104), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        super().paintEvent(event)


class AddUrlDialog(_AddTextDialog):
    """添加网页链接按钮。"""

    def __init__(self, pos=None, name="", url="", parent=None):
        super().__init__("添加网页链接", "按钮名称（留空自动取网址域名）",
                         "https://...", pos, name, url, parent)
        self.url_edit = self.value_edit   # 兼容旧引用

    def validate(self, value):
        if not value.lower().startswith(("http://", "https://")):
            return False, "网址必须以 http:// 或 https:// 开头"
        return True, ""

    def auto_name(self, value):
        return _hostname_of(value)


class AddCommandDialog(_AddTextDialog):
    """添加命令按钮：存为 run:// 前缀，点击时用 shell 执行。"""

    def __init__(self, pos=None, name="", cmd="", parent=None):
        super().__init__("添加命令", "按钮名称（留空取命令名）",
                         "例如：notepad.exe 或 python main.py",
                         pos, name, cmd, parent)
        self.cmd_edit = self.value_edit   # 兼容旧引用

    def validate(self, value):
        if not value:
            return False, "命令不能为空"
        return True, ""

    def auto_name(self, value):
        parts = value.split()
        if not parts:
            return "命令"
        return os.path.basename(parts[0]) or "命令"


class AddPathDialog(_AddTextDialog):
    """编辑路径/内容按钮：文件、UNC 路径等通用场景。"""

    def __init__(self, pos=None, name="", value="", parent=None):
        super().__init__("编辑路径", "按钮名称", "文件路径 / 网址 / 命令均可",
                         pos, name, value, parent)
        self.path_edit = self.value_edit

    def validate(self, value):
        return (bool(value.strip()), "" if value.strip() else "路径不能为空")

    def auto_name(self, value):
        b = os.path.basename(value.rstrip("/\\"))
        return b or "路径"


def _slot_type_label(item):
    """按 path 判断按钮类型（用于编辑菜单标签与编辑逻辑分派）。"""
    p = str((item or {}).get("path", ""))
    if p.startswith("run://"):
        return "命令"
    if p.lower().startswith(("http://", "https://")):
        return "网页链接"
    if p and os.path.isdir(p):
        return "文件夹"
    if p and os.path.exists(p):
        return "文件"
    if p.lower().startswith("mailto:"):
        return "邮件"
    return "路径"


def _run_edit_slot(global_pos, idx, get_item, set_item, parent=None):
    """按按钮类型打开对应编辑弹窗；set_item(idx, new_dict) 由调用方保存并重排。"""
    item = dict(get_item(idx) or {})
    path = str(item.get("path", ""))
    name = str(item.get("name", ""))
    icon = item.get("icon", "")
    if path.startswith("run://"):
        dlg = AddCommandDialog(pos=global_pos, name=name,
                               cmd=path[len("run://"):].lstrip("/"), parent=parent)
        if dlg.exec_() == QDialog.Accepted:
            n, v = dlg.result_pair()
            set_item(idx, {"name": n, "path": "run://" + v, "icon": icon})
    elif path.lower().startswith(("http://", "https://")):
        dlg = AddUrlDialog(pos=global_pos, name=name, url=path, parent=parent)
        if dlg.exec_() == QDialog.Accepted:
            n, v = dlg.result_pair()
            set_item(idx, {"name": n, "path": v, "icon": icon})
    elif os.path.isdir(path):
        new_path = QFileDialog.getExistingDirectory(parent, "选择文件夹", path)
        if new_path:
            set_item(idx, {"name": name or os.path.basename(new_path),
                           "path": new_path, "icon": icon})
    elif os.path.exists(path):
        new_path, _ = QFileDialog.getOpenFileName(
            parent, "选择文件或程序", os.path.dirname(path) or "",
            "程序 / 快捷方式 (*.exe *.lnk *.url *.bat *.cmd *.ps1);;所有文件 (*.*)")
        if new_path:
            set_item(idx, {"name": name or os.path.basename(new_path),
                           "path": new_path, "icon": icon})
    else:
        dlg = AddPathDialog(pos=global_pos, name=name, value=path, parent=parent)
        if dlg.exec_() == QDialog.Accepted:
            n, v = dlg.result_pair()
            set_item(idx, {"name": n, "path": v, "icon": icon})


def _make_globe_icon(size):
    """网址按钮默认图标：简单地球（圆 + 经纬线），透明背景。"""
    size = max(16, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = size * 0.40
    c = QPointF(size / 2.0, size / 2.0)
    pen = QPen(QColor(150, 178, 224, 235), max(1.0, size * 0.055))
    p.setPen(pen)
    p.setBrush(QColor(90, 130, 200, 70))
    p.drawEllipse(c, r, r)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, r * 0.72, r * 0.72)
    p.drawEllipse(c, r * 0.34, r * 0.34)
    p.drawEllipse(c, r * 0.05, r * 0.05)
    p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
    p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
    p.drawLine(QPointF(c.x() - r * 0.66, c.y() - r * 0.66),
               QPointF(c.x() + r * 0.66, c.y() + r * 0.66))
    p.drawLine(QPointF(c.x() - r * 0.66, c.y() + r * 0.66),
               QPointF(c.x() + r * 0.66, c.y() - r * 0.66))
    p.end()
    return pm


class Toast(QWidget):
    """轻提示：出现在鼠标附近，短暂显示后自动消失。"""
    def __init__(self, text, pos, duration=1600):
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lab = QLabel(text)
        lab.setStyleSheet(
            "color:#f2f4f8;font-family:'SimHei';font-size:10px;"
            "background:rgba(28,32,44,210);border-radius:6px;padding:6px 10px")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.addWidget(lab)
        self.adjustSize()
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = min(pos.x() + 10, g.right() - self.width() - 4)
        y = min(pos.y() + 16, g.bottom() - self.height() - 4)
        self.move(max(g.left() + 4, x), max(g.top() + 4, y))
        self.show()
        self.raise_()
        if duration and duration > 0:
            QTimer.singleShot(duration, self.close)


# ==================== 径向快捷启动菜单 ====================
class RadialMenu(QWidget):
    _btn_style = """
        QPushButton{background:rgba(255,255,255,220);border:1px solid rgba(200,210,230,180);border-radius:5px}
        QPushButton:hover{background:rgba(220,235,255,240);border:1px solid rgba(100,150,220,220)}
    """
    _HOVER_SCALE = 1.30
    _HOVER_ENTER_DUR = 0.10
    _HOVER_EXIT_DUR = 0.08
    _drag_room = 48  # 拖拽预留空间：窗口自展开起就留足，拖拽中绝不改窗口几何（避免偏移/崩溃）

    def __init__(self, pet_widget):
        super().__init__(None)
        self.pet = pet_widget
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.buttons = []          # 按钮数据列表（非 QWidget）
        self._btn_names = []
        self._btn_pixmaps = []     # 每个按钮的图标 QPixmap
        self._btn_shortcuts = []   # 每个按钮的快捷方式数据
        self.is_visible_state = False
        self._animating = False
        self._tip = _TipLabel()
        self._tip.hide()
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.setTimerType(Qt.PreciseTimer)
        self._anim_duration = 0.25
        self._stagger_delay = 0.0167  # 递进延迟（按钮/扇形通用）
        self._anim_elapsed = QElapsedTimer()
        self._target_positions = []
        self._center_pos = QPointF(0, 0)
        self._arc_start = -math.pi / 2
        self._arc_sweep = 2 * math.pi
        self._margin = 0
        self._icon_size = 0
        self._line_opacity = 0.0
        self._disk_opacity = 0.0  # 背景盘透明度（独立于按钮/分隔线动画）
        self._inside_ratio = 0.35  # 桌宠图标半径占展开半径的比例（决定按钮何时出现在边缘/收回何时透明）
        self._reorder_drag = -1  # 拖拽重排中按住的按钮索引，-1 表示无
        self._reorder_press = None
        self._reorder_btn = -1   # 待拖拽的按钮索引（按下时记录，超过阈值后激活为 _reorder_drag）
        self._hovered_btn = -1  # 鼠标悬停的按钮索引
        self._hovered_sector = -1  # 鼠标悬停的扇形索引（-1 表示无）
        self._sector_glow = {}  # 每个扇区的悬停高光进度 0..1（平滑过渡）
        self._sector_scale = 0.0  # 背景盘整体展开比例（所有扇形同步）
        self._glow_ts = 0.0  # 高光过渡上次更新时间（时间基准，与帧率无关）
        # 每个按钮的悬停时间戳（>0=enter时刻, <0=exit时刻, 0=无动画）
        self._btn_hover_ts = {}
        # 每个按钮的动画绘制状态：(x, y, scale_ratio, alpha)
        self._btn_anim_state = {}
        self._normal_sizes = []  # 每个按钮的正常尺寸
        self._reverse_from = None  # 反向衔接时的起始 progress 列表
        self._reverse_dir = None   # 'show' 或 'hide'，None 表示正常动画
        self._empty_menu = False  # 无插槽时只显示背景盘 + 提示
        self.setAcceptDrops(True)  # 支持拖入文件添加/替换按钮
        self._drop_over_disk = False  # 外部拖入悬停背景盘
        self._drop_glow = 0.0         # 背景盘外缘高光进度 0..1
        self._drop_glow_ts = 0.0
        self._rearrange_active = False  # 删除/添加/交换后的平滑重排动画
        self._rearrange_elapsed = QElapsedTimer()
        self._rearrange_dur = 0.3
        self._rearrange_from = []
        self._rearrange_to = []
        self._rearrange_new = set()
        self._layout_radius = 0  # 最近一次布局的按钮半径（用于重排去重）
        self._fan_e0 = None      # 空分区顺时针侧边界（贴边扇形布局用）
        self._fan_free = 2 * math.pi
        self._fan_slot0 = 0
        self._reorder_toast = None     # 拖拽操作的气泡提示
        self._reorder_toast_text = None
        self._reorder_btn = -1     # 按下待拖拽的按钮索引
        self._reorder_press = None
        self._reorder_target = -1
        self._reorder_local = None
        self._reorder_outside = False
        self.hide()

    def toggle_menu(self, shortcuts):
        try:
            # 反转目标状态：展开 → 收回；收回 → 展开。打断时基于当前进度衔接。
            if self._animating:
                # 正在动画中：根据 is_visible_state（下一次的目标）决定新目标
                # is_visible_state==True 表示正在展开，反转则收回；反之亦然
                if self.is_visible_state:
                    self._reverse_to_hide()
                else:
                    # 正在收回中，但用户想展开：用现有 shortcuts 重新展开
                    if isinstance(shortcuts, list):
                        self._reverse_to_show(shortcuts)
                return
            if self.is_visible_state:
                self.hide_menu()
            else:
                if isinstance(shortcuts, list):
                    self.show_menu(shortcuts)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.hide_menu(animate=False)

    def _capture_anim_progress(self):
        # 基于当前 elapsed 计算每个按钮的进度 local_t（0..1）与当前 opacity / 位置
        if not self._anim_elapsed.isValid():
            return None
        elapsed = self._anim_elapsed.elapsed() / 1000.0
        per_btn = []
        for i in range(len(self.buttons)):
            delay = self._stagger_delay * i if self.is_visible_state else 0.0
            local_t = max(0.0, min((elapsed - delay) / self._anim_duration, 1.0))
            per_btn.append(local_t)
        return per_btn

    def _reverse_to_hide(self):
        # 正在展开 → 切换为收回：记录当前各按钮 scale，按顺序递进收回（无跳变）
        prog = [self._btn_anim_state.get(i, (0, 0, 0, 0))[2] for i in range(len(self.buttons))]
        self._reverse_from = prog or [1.0] * len(self.buttons)  # 当前 scale（0..1）
        self._reverse_dir = 'hide'
        self.is_visible_state = False
        self._animating = True
        self._rearrange_active = False
        self._anim_elapsed.start()
        # 保持 _anim_timer 继续运行
        if not self._anim_timer.isActive():
            self._anim_timer.start(16)
        self.pet.trigger_elastic('close')

    def _reverse_to_show(self, shortcuts):
        # 正在收回且按钮已经被清掉 → 直接重建并立即展开
        if not self.buttons:
            self.show_menu(shortcuts)
            return
        # 快速切换时直接重新展开，保证递进延迟效果
        if not self.buttons:
            self.show_menu(shortcuts)
            return
        # 重置所有按钮到中心位置，确保递进延迟从头播放
        cx, cy = self._center_pos.x(), self._center_pos.y()
        for i in range(len(self.buttons)):
            self._btn_anim_state[i] = (cx, cy, 0.01, 0.0)
        self._reverse_from = None
        self._reverse_dir = None
        self.is_visible_state = True
        self._animating = True
        self._rearrange_active = False
        self._anim_elapsed.start()
        if not self._anim_timer.isActive():
            self._anim_timer.start(16)
        self.pet.trigger_elastic('open')

    def _compute_free_arc(self, radius, icon_size):
        """给定半径下，按钮中心可放置的最大连续可用扇形（2° 采样，带边界余量）"""
        step = 180  # 2° 采样，边界更精确
        blocked = []
        for i in range(step):
            a = -math.pi / 2 + 2 * math.pi * i / step
            blocked.append(self._angle_blocked(a, radius, icon_size))

        if not any(blocked):
            return -math.pi / 2, 2 * math.pi

        best_start = best_len = cur_start = cur_len = 0
        for k in range(step * 2):
            idx = k % step
            if not blocked[idx]:
                if cur_len == 0:
                    cur_start = idx
                cur_len += 1
                if cur_len > best_len:
                    best_start, best_len = cur_start, cur_len
            else:
                cur_len = 0
        best_len = min(best_len, step)
        return -math.pi / 2 + 2 * math.pi * best_start / step, 2 * math.pi * best_len / step

    def _angle_blocked(self, angle, radius, icon_size):
        """该角度上的按钮中心位置是否越出屏幕。"""
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            pg = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.height() // 2))
            ex = pg.x() + radius * math.cos(angle)
            ey = pg.y() + radius * math.sin(angle)
            bh = icon_size // 2 + 2
            return (ex - bh < screen.left() or ex + bh > screen.right()
                    or ey - bh < screen.top() or ey + bh > screen.bottom())
        except Exception:
            return False

    def _button_angles(self, n, radius, start_a, sweep, icon_size, align_to=None):
        """生成 n 个按钮的角度（弧度），并记录空分区布局参数。

        整圆：从正上方开始顺时针均匀分布。
        贴边部分扇形：不整体旋转，按钮按原顺序朝空分区两侧挤压：
        - 遮挡中心 B，找最近的"规范间隙中点"Bc（相邻插槽之间）；
        - 空分区中心 C 从 Bc 向 B 平滑过渡、宽度 W 从"遮挡角+2×偏移"收缩
          到"遮挡角本身"——贴边初期不转圈，贴深/吸附后按钮均匀铺满内侧
          角平分方向（如单边吸附即铺满内侧 180°）；
        - 空分区之外的夹角 (2π-W) 均匀分给 n 个按钮分区，按钮位于分区中心。
        """
        is_full = sweep >= 2 * math.pi - 0.01
        if is_full:
            self._fan_e0 = None
            self._fan_free = 2 * math.pi
            self._fan_slot0 = 0
            return [-math.pi / 2 + 2 * math.pi * k / n for k in range(n)]
        G = 2 * math.pi - sweep                      # 遮挡角
        B = (start_a + sweep / 2 + math.pi) % (2 * math.pi)  # 遮挡中心
        # 最近的规范间隙中点（仅用于初次展开时的默认弧线起点，保证顺序不翻转）
        s, d = 0, 1e9
        for j in range(n):
            mid = (-math.pi / 2 + math.pi / n + 2 * math.pi * j / n) % (2 * math.pi)
            dd = abs((mid - B + math.pi) % (2 * math.pi) - math.pi)
            if dd < d:
                s, d = j, dd
        # 空分区始终正对屏幕边缘：中心=遮挡中心、宽度=遮挡角
        W = max(G, 0.04)
        W = min(W, 2 * math.pi - 0.3)                # 给按钮留最小空间
        F = 2 * math.pi - W                          # 按钮分区总夹角
        E0 = (B + W / 2) % (2 * math.pi)             # 空分区顺时针侧边界
        base = [(E0 + F * (j + 0.5) / n) % (2 * math.pi) for j in range(n)]
        # 弧线起点对应哪个插槽：拖动重排时用当前位置选最接近的循环移位，
        # 保证按钮内容不互换、不跳位；初次展开默认取缝隙后的第一个插槽
        best = None
        if align_to and len(align_to) == n:
            for shift in range(n):
                cost = sum(
                    (abs((base[(j - shift) % n] - align_to[j] + math.pi)
                         % (2 * math.pi) - math.pi)) ** 2
                    for j in range(n))
                if best is None or cost < best[0]:
                    best = (cost, shift)
        shift = best[1] if best is not None else (s + 1) % n
        self._fan_e0 = E0
        self._fan_free = F
        self._fan_slot0 = shift % n
        angles = [0.0] * n
        for k in range(n):
            m = (k - shift) % n
            angles[k] = (E0 + F * (m + 0.5) / n) % (2 * math.pi)
        return angles

    def _pick_layout(self, n, base_radius, icon_size):
        """边缘感知布局：菜单盘大小固定，只在遮挡范围外找最大连续扇形。

        半径保持基础值不变（贴边/吸附时菜单盘不放大）；返回 (radius, start_a, sweep)。
        """
        r = int(base_radius)
        sa, sw = self._compute_free_arc(r, icon_size)
        if sw < 0.01:
            sw = 2 * math.pi
            sa = -math.pi / 2
        return r, sa, sw

    def show_menu(self, shortcuts):
        try:
            # 菜单展开时保留气泡：只确保气泡在菜单上方（z 序）。
            # 位置交给桌宠帧循环每帧 _place() 平滑跟随，不再在这里 _relayout/_place，
            # 否则点击展开菜单时气泡会朝桌宠中间瞬跳一下。
            try:
                if self.pet.status_bubble.isVisible():
                    self.pet.status_bubble.raise_()
            except Exception:
                pass
            self.buttons.clear()
            self._btn_names.clear()
            self._btn_pixmaps.clear()
            self._btn_shortcuts.clear()
            self._tip.hide()
            if not shortcuts:
                # 空菜单：只显示背景盘 + "拖入添加"提示
                ps = self.pet.pet_size
                icon_size = max(16, int(self.pet.settings.get("button_size", 25) * _kit.pet_scale()))
                radius = ps * 0.55 + icon_size * 0.8
                margin = int(radius + icon_size + 10 + self._drag_room)
                self._margin = margin
                self._reposition_geometry(margin, margin * 2)
                self._center_pos = QPointF(margin, margin)
                self._target_positions = []
                self._arc_start = -math.pi / 2
                self._arc_sweep = 2 * math.pi
                self._icon_size = icon_size
                self._inside_ratio = max(0.1, min(0.6, (ps * 0.5) / radius))
                self._empty_menu = True
                self._btn_anim_state.clear()
                self._sector_scale = 0.0
                self.is_visible_state = True
                self._animating = True
                self._rearrange_active = False
                self._reverse_from = None
                self._reverse_dir = None
                self._anim_elapsed.start()
                self._anim_timer.start(16)
                self.setWindowOpacity(self.pet.pet_opacity)
                self.show()
                self.pet.raise_()
                self.pet.trigger_elastic('open')
                return

            ps = self.pet.pet_size
            icon_size = max(16, int(self.pet.settings.get("button_size", 25) * _kit.pet_scale()))
            n = min(len(shortcuts), 8)
            shortcuts = shortcuts[:n]

            radius = ps * 0.55 + icon_size * 0.8
            radius, start_a, sweep = self._pick_layout(n, radius, icon_size)

            # 防止sweep为0导致除以零错误
            if sweep < 0.01:
                sweep = 2 * math.pi
                start_a = -math.pi / 2
            self._arc_start = start_a
            self._arc_sweep = sweep

            margin = int(radius + icon_size + 10 + self._drag_room)
            self._margin = margin
            self._reposition_geometry(margin, margin * 2)
            cx = cy = margin
            self._center_pos = QPointF(cx, cy)
            self._target_positions = []
            angles = self._button_angles(n, radius, start_a, sweep, icon_size)
            for i, sc in enumerate(shortcuts):
                angle = angles[i]
                self._target_positions.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

                icon = None
                ci = sc.get("icon", "")
                if ci and os.path.exists(ci):
                    try:
                        icon = QPixmap(ci).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    except Exception:
                        pass
                if not icon or icon.isNull():
                    try:
                        icon = extract_exe_icon(sc.get("path", ""), 256)
                    except Exception:
                        pass
                if (not icon or icon.isNull()) and \
                        str(sc.get("path", "")).lower().startswith(("http://", "https://")):
                    # 网址按钮：画一个地球占位，避免空白
                    icon = _make_globe_icon(256)
                self._btn_pixmaps.append(icon if icon and not icon.isNull() else None)
                self._btn_shortcuts.append(sc)
                self._btn_names.append(sc.get("name", ""))
                self.buttons.append(True)  # 占位，仅用于 len() 和遍历

            self._icon_size = icon_size
            self._empty_menu = False
            self._hovered_btn = -1
            self._hovered_sector = -1
            self._sector_glow = {}
            self._sector_scale = 0.0
            self._btn_hover_ts.clear()
            _prewarm_lnk_cache([sc.get("path", "") for sc in shortcuts])
            # 计算"桌宠图标半径占展开半径"的比，作为按钮位置触及桌宠边缘的临界
            if radius > 0:
                self._inside_ratio = max(0.1, min(0.6, (ps * 0.5) / radius))
            self.is_visible_state = True
            self._animating = True
            self._rearrange_active = False
            self._reverse_from = None
            self._reverse_dir = None
            self._anim_elapsed.start()
            self._anim_timer.start(16)
            # 初始化每个按钮的绘制状态：从中心开始，极小、全透明
            for i in range(len(self.buttons)):
                self._btn_anim_state[i] = (cx, cy, 0.01, 0.0)
            self.setWindowOpacity(self.pet.pet_opacity)
            self.show()
            self.pet.raise_()
            self.pet.trigger_elastic('open')
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.hide_menu(animate=False)

    def hide_menu(self, animate=True):
        # 已经完全隐藏或者在描重路径上：返回
        if not self.is_visible_state and not self._animating:
            return
        # 正在动画中：让 toggle_menu 处理打断，hide_menu 不强行重启
        if self._animating and animate and self.is_visible_state:
            return
        if not animate:
            self._anim_timer.stop()
            self._animating = False
            self._rearrange_active = False
            self.is_visible_state = False
            self._hovered_btn = -1
            self._hovered_sector = -1
            self._sector_glow.clear()
            self._btn_hover_ts.clear()
            self._btn_anim_state.clear()
            self._reverse_from = None
            self._reverse_dir = None
            self.buttons.clear()
            self._btn_names.clear()
            self._btn_pixmaps.clear()
            self._btn_shortcuts.clear()
            self._tip.hide()
            if getattr(self, '_reorder_toast', None) is not None:
                self._reorder_toast.close()
                self._reorder_toast = None
            self.hide()
            self.setWindowOpacity(1.0)
            return
        self.is_visible_state = False
        self._animating = True
        self._rearrange_active = False
        self._reverse_from = None
        self._reverse_dir = None
        self._hovered_btn = -1
        self._hovered_sector = -1
        self._sector_glow.clear()
        self._btn_hover_ts.clear()
        self._btn_anim_state.clear()
        self._normal_sizes.clear()
        self._anim_elapsed.start()
        self._anim_timer.start(16)
        if getattr(self, '_reorder_toast', None) is not None:
            self._reorder_toast.close()
            self._reorder_toast = None

    def _reposition_geometry(self, margin, total_size):
        pc = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.height() // 2))
        self.setGeometry(pc.x() - margin, pc.y() - margin, total_size, total_size)

    def _start_hover_anim(self, btn_index):
        """记录悬停按钮，paintEvent 驱动放大动画"""
        if not self.buttons or not self.is_visible_state:
            return
        if self._hovered_btn == btn_index:
            return
        # 旧按钮开始缩小
        if self._hovered_btn >= 0:
            self._btn_hover_ts[self._hovered_btn] = -time.monotonic()
        self._hovered_btn = btn_index
        self._btn_hover_ts[btn_index] = time.monotonic()
        self.update()

    def _stop_hover_anim(self):
        """鼠标离开所有按钮，paintEvent 驱动缩小动画"""
        if self._hovered_btn < 0:
            return
        self._btn_hover_ts[self._hovered_btn] = -time.monotonic()
        self._hovered_btn = -1
        self.update()

    def _tick_anim(self):
        elapsed = self._anim_elapsed.elapsed() / 1000.0
        # 平滑重排：删除/添加/交换后，按钮从当前位置滑向新目标（分区随目标过渡）
        if self._rearrange_active:
            k = ease_out_cubic(min(1.0, self._rearrange_elapsed.elapsed() / 1000.0 / max(self._rearrange_dur, 0.01)))
            for i in range(len(self.buttons)):
                fx, fy = self._rearrange_from[i]
                tx, ty = self._rearrange_to[i]
                mx, my = fx + (tx - fx) * k, fy + (ty - fy) * k
                self._target_positions[i] = (mx, my)
                st = self._btn_anim_state.get(i)
                if i in self._rearrange_new:
                    sc_ = ease_out_back(k)
                    cur_scale = max(0.0, sc_)
                    cur_alpha = max(0.0, min(1.0, (sc_ - self._inside_ratio) / max(0.05, 1 - self._inside_ratio)))
                    self._btn_anim_state[i] = (mx, my, cur_scale, cur_alpha)
                elif st:
                    self._btn_anim_state[i] = (mx, my, st[2], st[3])
            if k >= 1.0:
                self._target_positions = list(self._rearrange_to)
                self._rearrange_active = False
                self._anim_timer.stop()
            self.update()
            return
        cx, cy = self._center_pos.x(), self._center_pos.y()
        fs = self._icon_size
        n = len(self.buttons)
        all_done = True
        reverse = self._reverse_dir is not None
        reverse_from = self._reverse_from or [0.0] * n
        # 背景盘扇形整体进度：所有扇形同步展开/收起（不跟随按钮递进）
        # 展开比第一个按钮慢 0.1s；收回比最后一个按钮慢 0.1s
        if reverse:
            sp = max(0.0, min(1.0, (elapsed - DISK_ANIM_DELAY) / self._anim_duration))
            if self.is_visible_state:
                self._sector_scale += (1.0 - self._sector_scale) * ease_out_cubic(sp)
            else:
                self._sector_scale *= (1.0 - ease_out_cubic(sp))
        else:
            if self.is_visible_state:
                sp = max(0.0, min(1.0, (elapsed - DISK_ANIM_DELAY) / self._anim_duration))
                self._sector_scale = ease_out_back(sp)
            else:
                last_delay = self._stagger_delay * max(0, n - 1)
                sp = max(0.0, min(1.0, (elapsed - (last_delay + DISK_ANIM_DELAY)) / self._anim_duration))
                self._sector_scale = 1.0 - ease_out_cubic(sp)
        # 阶段透明度（分隔线/背景盘）
        if reverse:
            # 反向衔接：根据当前 _reverse_dir 决定整体方向，使用 0..1 的"重组进度"
            progress = min(1.0, elapsed / max(self._anim_duration, 0.05))
        else:
            progress = min(1.0, elapsed / self._anim_duration)
        if self.is_visible_state:
            # 目标=展开。透明度从 reverse_from → 1
            if reverse:
                self._line_opacity = smoothstep(progress)
                self._disk_opacity = smoothstep(progress) * 0.55
            else:
                self._line_opacity = min(1.0, max(0.0, (elapsed - 0.03) / 0.12))
                self._disk_opacity = smoothstep(progress) * 0.55
        else:
            # 目标=收回
            if reverse:
                self._line_opacity = max(0.0, 1.0 - smoothstep(progress))
                self._disk_opacity = max(0.0, 1.0 - smoothstep(progress)) * 0.55
            else:
                self._line_opacity = max(0.0, 1.0 - elapsed / 0.08)
                self._disk_opacity = smoothstep(max(0.0, 1.0 - progress)) * 0.55
        for i, btn in enumerate(self.buttons):
            if i < len(self._target_positions):
                delay = self._stagger_delay * i
                tx, ty = self._target_positions[i]
                if reverse and not self.is_visible_state:
                    # 反向收回：未轮到的按钮继续完成展开动画，轮到收回时从当前位置平滑衔接
                    if elapsed < delay:
                        rf = reverse_from[i] if i < len(reverse_from) else 0.0
                        k = ease_out_cubic(min(1.0, elapsed / max(delay, 0.001)))
                        cur_s = rf + (1.0 - rf) * k
                        p_i = 0.0
                    else:
                        p_i = max(0.0, min(1.0, (elapsed - delay) / self._anim_duration))
                        cur_s = 1.0 - ease_out_cubic(p_i)
                    t = 1.0 - cur_s
                    cur_x, cur_y = tx + (cx - tx) * t, ty + (cy - ty) * t
                    cur_scale = max(0.0, cur_s)
                    local_t = p_i
                    dist_ratio = max(0.0, cur_scale)
                else:
                    if reverse:
                        # 反向展开（兼容保留）：从 reverse_from 插值到 1
                        p_i = max(0.0, min(1.0, (elapsed - delay) / self._anim_duration))
                        start = reverse_from[i] if i < len(reverse_from) else 0.0
                        local_t = start + (1.0 - start) * p_i
                    else:
                        local_t = max(0.0, min((elapsed - delay) / self._anim_duration, 1.0))
                    if self.is_visible_state:
                        t = ease_out_back(local_t)
                        dist_ratio = abs(t)
                        cur_x, cur_y = cx + (tx - cx) * t, cy + (ty - cy) * t
                        cur_scale = max(0.0, t)
                    else:
                        t = ease_out_cubic(local_t)
                        dist_ratio = 1.0 - abs(t)
                        cur_x, cur_y = tx + (cx - tx) * t, ty + (cy - ty) * t
                        cur_scale = max(0.0, 1.0 - t)
                if local_t < 1.0:
                    all_done = False
                inside_ratio = self._inside_ratio
                per_btn_alpha = max(0.0, min(1.0, (dist_ratio - inside_ratio) / max(0.05, (1 - inside_ratio))))
                if len(self._normal_sizes) <= i:
                    self._normal_sizes.extend([0] * (i + 1 - len(self._normal_sizes)))
                self._normal_sizes[i] = max(4, int(fs * cur_scale)) if cur_scale > 0 else 0
                self._btn_anim_state[i] = (cur_x, cur_y, cur_scale, per_btn_alpha)

        # 窗口透明度固定为桌宠透明度（按钮 alpha 由 _btn_anim_state 独立控制）
        self.setWindowOpacity(self.pet.pet_opacity)
        self.update()

        # 所有按钮完成各自动画、且背景盘也完成（盘比首/末按钮慢 0.1s）后才结束
        sector_done = (self._sector_scale >= 0.999) if self.is_visible_state else (self._sector_scale <= 0.01)
        finished = all_done and sector_done
        if finished:
            self._reverse_from = None
            self._reverse_dir = None
            if self.is_visible_state:
                self._animating = False
                self._anim_timer.stop()
            else:
                self._anim_timer.stop()
                self.hide()
                self.setWindowOpacity(1.0)
                self._animating = False

    def _launch(self, path):
        path = str(path or "")
        if not path:
            self.hide_menu()
            return
        try:
            if path.startswith("run://"):
                # 命令按钮：run:// 后面的内容交给 shell 执行
                subprocess.Popen(path[len("run://"):].lstrip("/"), shell=True,
                                 creationflags=0x08000000)
            elif path.lower().startswith(("http://", "https://", "mailto:", "tel:")):
                webbrowser.open(path)
            elif os.path.exists(path) and os.path.splitext(path)[1].lower() in (".py", ".pyw"):
                subprocess.Popen([sys.executable, path])
            elif os.path.exists(path):
                os.startfile(path)
            else:
                # 命令行 或 自定义脚本：走 shell
                subprocess.Popen(path, shell=True, creationflags=0x08000000)
        except Exception:
            try:
                subprocess.Popen(path, shell=True, creationflags=0x08000000)
            except Exception:
                logger.exception("Failed to launch: %s", path)
        self.hide_menu()

    def _reshow_menu(self):
        if hasattr(self, '_pending_sc') and self._pending_sc:
            self.show_menu(self._pending_sc)
            self._pending_sc = []

    def _close_reorder_toast(self):
        self._reorder_toast_text = None
        if getattr(self, '_reorder_toast', None) is not None:
            try:
                self._reorder_toast.close()
            except Exception:
                pass
            self._reorder_toast = None

    def rearrange(self, shortcuts):
        """删除/添加/交换后平滑过渡到新布局：按钮从当前位置滑向新目标，分区同步过渡"""
        try:
            if not shortcuts:
                self.hide_menu()
                return
            # 记录当前按钮位置（按路径/名称匹配，跨索引保持身份）
            old_pos, old_scale, old_alpha = {}, {}, {}
            old_cx, old_cy = self._center_pos.x(), self._center_pos.y()
            for i, st in self._btn_anim_state.items():
                if i >= len(self._btn_shortcuts):
                    continue
                sc = self._btn_shortcuts[i]
                key = (sc.get("path", ""), sc.get("name", ""))
                old_pos[key] = (st[0], st[1])
                old_scale[key] = st[2]
                old_alpha[key] = st[3]

            ps = self.pet.pet_size
            icon_size = max(16, int(self.pet.settings.get("button_size", 25) * _kit.pet_scale()))
            n = min(len(shortcuts), 8)
            shortcuts = shortcuts[:n]
            radius = ps * 0.55 + icon_size * 0.8
            radius, start_a, sweep = self._pick_layout(n, radius, icon_size)
            if sweep < 0.01:
                sweep = 2 * math.pi
                start_a = -math.pi / 2
            self._arc_start = start_a
            self._arc_sweep = sweep
            margin = int(radius + icon_size + 10 + self._drag_room)
            self._margin = margin
            self._reposition_geometry(margin, margin * 2)
            cx = cy = margin
            self._center_pos = QPointF(cx, cy)
            off_x, off_y = cx - old_cx, cy - old_cy
            old_pos = {k: (v[0] + off_x, v[1] + off_y) for k, v in old_pos.items()}
            self._target_positions = []
            angles = self._button_angles(n, radius, start_a, sweep, icon_size)
            new_names, new_pixmaps, new_shortcuts = [], [], []
            for i, sc in enumerate(shortcuts):
                angle = angles[i]
                self._target_positions.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
                icon = None
                ci = sc.get("icon", "")
                if ci and os.path.exists(ci):
                    try:
                        icon = QPixmap(ci).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    except Exception:
                        pass
                if not icon or icon.isNull():
                    try:
                        icon = extract_exe_icon(sc.get("path", ""), 256)
                    except Exception:
                        pass
                new_pixmaps.append(icon if icon and not icon.isNull() else None)
                new_shortcuts.append(sc)
                new_names.append(sc.get("name", ""))
            self.buttons = [True] * n
            self._btn_names = new_names
            self._btn_pixmaps = new_pixmaps
            self._btn_shortcuts = new_shortcuts
            self._icon_size = icon_size
            self._empty_menu = False
            if radius > 0:
                self._inside_ratio = max(0.1, min(0.6, (ps * 0.5) / radius))
            if self._hovered_sector >= n:
                self._hovered_sector = -1
            # 每个新索引的起始位置：保留现有按钮动画状态，新按钮从中心出现
            self._rearrange_active = True
            self._rearrange_elapsed = QElapsedTimer()
            self._rearrange_elapsed.start()
            self._rearrange_from = []
            self._rearrange_new = set()
            for i, sc in enumerate(shortcuts):
                key = (sc.get("path", ""), sc.get("name", ""))
                if key in old_pos:
                    self._rearrange_from.append(old_pos[key])
                    self._btn_anim_state[i] = (old_pos[key][0], old_pos[key][1],
                                               old_scale.get(key, 1.0), old_alpha.get(key, 1.0))
                else:
                    self._rearrange_from.append((cx, cy))
                    self._rearrange_new.add(i)
                    self._btn_anim_state[i] = (cx, cy, 0.01, 0.0)
            self._rearrange_to = list(self._target_positions)
            self._normal_sizes = [int(icon_size)] * n
            self._anim_timer.start(16)
            self.show()
            self.pet.raise_()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.show_menu(shortcuts)

    def reflow(self):
        """位置变化（拖动/吸附）时：按当前屏幕位置重新计算布局并平滑过渡。

        用于桌宠展开菜单时被拖到屏幕边缘/角落的场景：按钮与扇形分区重新
        均匀分布，并带过渡动画，方便观察排列是否合理。
        """
        if not self.is_visible_state or not self.buttons:
            return
        ps = self.pet.pet_size
        icon_size = self._icon_size or max(16, int(self.pet.settings.get("button_size", 25) * _kit.pet_scale()))
        n = len(self.buttons)
        radius, start_a, sweep = self._pick_layout(n, ps * 0.55 + icon_size * 0.8, icon_size)
        if sweep < 0.01:
            sweep = 2 * math.pi
            start_a = -math.pi / 2
        # 布局未变化：跳过（避免拖动过程中反复重启动画）
        if (radius == self._layout_radius
                and abs(sweep - self._arc_sweep) < 0.02
                and abs(start_a - self._arc_start) < 0.02):
            return
        self._layout_radius = radius
        self._arc_start = start_a
        self._arc_sweep = sweep
        old_cx, old_cy = self._center_pos.x(), self._center_pos.y()
        # 当前按钮角度（相对旧中心），用于连续对齐避免瞬间跳动
        align_to = []
        for i in range(n):
            st = self._btn_anim_state.get(i)
            if st:
                align_to.append(math.atan2(st[1] - old_cy, st[0] - old_cx))
            else:
                align_to.append(0.0)
        margin = int(radius + icon_size + 10 + self._drag_room)
        self._margin = margin
        self._reposition_geometry(margin, margin * 2)
        cx = cy = margin
        self._center_pos = QPointF(cx, cy)
        off_x, off_y = cx - old_cx, cy - old_cy
        angles = self._button_angles(n, radius, start_a, sweep, icon_size, align_to or None)
        new_targets = [(cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles]
        # 从当前位置平滑滑向新目标
        self._rearrange_active = True
        self._rearrange_elapsed = QElapsedTimer()
        self._rearrange_elapsed.start()
        self._rearrange_dur = 0.32
        self._rearrange_new = set()
        self._rearrange_from = []
        self._rearrange_to = list(new_targets)
        for i in range(n):
            st = self._btn_anim_state.get(i)
            if st:
                self._rearrange_from.append((st[0] + off_x, st[1] + off_y))
            else:
                self._rearrange_from.append((cx, cy))
        self._target_positions = list(new_targets)
        self._normal_sizes = [int(icon_size)] * n
        self._anim_timer.start(16)
        self.update()

    def _btn_context_menu(self, global_pos, idx):
        if idx < 0 or idx >= len(self._btn_shortcuts):
            return
        sc = self.pet.settings.get("slot_shortcuts", [])
        if idx >= len(sc):
            return
        menu = StyledMenu()
        a_edit = menu.addAction("编辑…（%s）" % _slot_type_label(sc[idx]))
        menu.addSeparator()
        a_rename = menu.addAction("重命名")
        a_icon = menu.addAction("更换图标")
        a_remove = menu.addAction("删除")
        chosen = menu.exec_(global_pos)
        if chosen == a_edit:
            self._edit_slot(global_pos, idx)
        elif chosen == a_rename:
            name, ok = QInputDialog.getText(self, "重命名", "名称:",
                                            text=sc[idx].get("name", ""))
            if ok and name:
                sc[idx]["name"] = name
                self.pet.settings["slot_shortcuts"] = sc
                save_settings(self.pet.settings)
                self.rearrange(sc)
        elif chosen == a_icon:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择图标图片", "",
                "图片 (*.png *.ico *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*.*)")
            if path:
                sc[idx]["icon"] = path
                self.pet.settings["slot_shortcuts"] = sc
                save_settings(self.pet.settings)
                self.rearrange(sc)
        elif chosen == a_remove:
            sc.pop(idx)
            self.pet.settings["slot_shortcuts"] = sc
            save_settings(self.pet.settings)
            self.rearrange(sc)

    def _edit_slot(self, global_pos, idx):
        """按按钮类型打开对应编辑弹窗（网页链接/命令/文件/文件夹/路径）。"""
        sc = self.pet.settings.get("slot_shortcuts", [])
        if idx < 0 or idx >= len(sc):
            return

        def set_item(i, new):
            sc[i] = new
            self.pet.settings["slot_shortcuts"] = sc
            save_settings(self.pet.settings)
            self.rearrange(sc)

        _run_edit_slot(global_pos, idx, lambda i: sc[i], set_item, self)

    def _empty_context_menu(self, global_pos):
        """盘面空白处右键菜单：支持多种格式的添加按钮。"""
        menu = StyledMenu()
        a_url = menu.addAction("网页链接…")
        a_file = menu.addAction("文件 / 程序…")
        a_dir = menu.addAction("文件夹…")
        a_cmd = menu.addAction("命令…")
        menu.addSeparator()
        a_clear = menu.addAction("清空全部按钮")
        chosen = menu.exec_(global_pos)
        if chosen == a_url:
            self._add_url_slot(global_pos)
        elif chosen == a_file:
            self._add_file_slot(global_pos)
        elif chosen == a_dir:
            self._add_dir_slot(global_pos)
        elif chosen == a_cmd:
            self._add_cmd_slot(global_pos)
        elif chosen == a_clear:
            self._clear_all_slots(global_pos)

    def _add_url_slot(self, global_pos):
        """弹窗输入名称 + 网址，追加为网页链接按钮（最多 8 个）。"""
        slots = list(self.pet.settings.get("slot_shortcuts", []))
        if len(slots) >= 8:
            Toast("插槽已满（最多 8 个）", global_pos)
            return
        dlg = AddUrlDialog(pos=global_pos)
        if dlg.exec_() == QDialog.Accepted:
            name, url = dlg.result_pair()
            if not url:
                return
            slots.append({"name": name, "path": url})
            self.pet.settings["slot_shortcuts"] = slots
            save_settings(self.pet.settings)
            self.rearrange(slots)

    def _add_file_slot(self, global_pos):
        """文件选择器添加文件 / 程序 / 快捷方式按钮。"""
        slots = list(self.pet.settings.get("slot_shortcuts", []))
        if len(slots) >= 8:
            Toast("插槽已满（最多 8 个）", global_pos)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件或程序", "",
            "程序 / 快捷方式 (*.exe *.lnk *.url *.bat *.cmd *.ps1);;所有文件 (*.*)")
        if path:
            name = os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)
            slots.append({"name": name, "path": path})
            self.pet.settings["slot_shortcuts"] = slots
            save_settings(self.pet.settings)
            self.rearrange(slots)

    def _add_dir_slot(self, global_pos):
        """文件夹选择器添加文件夹按钮。"""
        slots = list(self.pet.settings.get("slot_shortcuts", []))
        if len(slots) >= 8:
            Toast("插槽已满（最多 8 个）", global_pos)
            return
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if path:
            slots.append({"name": os.path.basename(path) or path, "path": path})
            self.pet.settings["slot_shortcuts"] = slots
            save_settings(self.pet.settings)
            self.rearrange(slots)

    def _add_cmd_slot(self, global_pos):
        """命令弹窗添加命令按钮（run:// 前缀，点击用 shell 执行）。"""
        slots = list(self.pet.settings.get("slot_shortcuts", []))
        if len(slots) >= 8:
            Toast("插槽已满（最多 8 个）", global_pos)
            return
        dlg = AddCommandDialog(pos=global_pos)
        if dlg.exec_() == QDialog.Accepted:
            name, cmd = dlg.result_pair()
            if not cmd:
                return
            slots.append({"name": name, "path": "run://" + cmd})
            self.pet.settings["slot_shortcuts"] = slots
            save_settings(self.pet.settings)
            self.rearrange(slots)

    def _clear_all_slots(self, global_pos):
        """清空全部按钮（带确认）。"""
        if not self.pet.settings.get("slot_shortcuts"):
            Toast("当前没有按钮", global_pos)
            return
        cp = ConfirmPopup("确定清空全部按钮？此操作不可撤销")
        if cp.exec_(global_pos):
            self.pet.settings["slot_shortcuts"] = []
            save_settings(self.pet.settings)
            self.rearrange([])

    def paintEvent(self, event):
        if not self.buttons and not self._empty_menu:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.buttons)
        tp = self._target_positions
        cx = self._center_pos.x()
        cy = self._center_pos.y()
        need_next_frame = False

        # 扇形悬停高光：按真实经过时间过渡，与帧率无关，颜色与外延都渐变
        glow_changed = False
        now_glow = time.monotonic()
        dt_glow = min(0.05, max(0.0, now_glow - self._glow_ts)) if self._glow_ts else 0.0
        self._glow_ts = now_glow
        if dt_glow > 0:
            for i in range(n):
                target = 1.0 if i == self._hovered_sector else 0.0
                cur = self._sector_glow.get(i, 0.0)
                if abs(cur - target) > 0.001:
                    rate = 12.0 if target > cur else 6.0  # 消失比出现慢一半
                    cur += (target - cur) * min(1.0, dt_glow * rate)
                    if abs(cur - target) < 0.001:
                        cur = target
                    self._sector_glow[i] = cur
                    glow_changed = True
        if glow_changed:
            need_next_frame = True

        # 拖入背景盘高光：淡入淡出（时间驱动，与帧率无关）
        now_dg = time.monotonic()
        dt_dg = min(0.05, max(0.0, now_dg - self._drop_glow_ts)) if self._drop_glow_ts else 0.0
        self._drop_glow_ts = now_dg
        target_dg = 1.0 if self._drop_over_disk else 0.0
        if abs(self._drop_glow - target_dg) > 0.001:
            rate_dg = 8.0 if target_dg > self._drop_glow else 5.0
            self._drop_glow += (target_dg - self._drop_glow) * min(1.0, dt_dg * rate_dg)
            if abs(self._drop_glow - target_dg) < 0.001:
                self._drop_glow = target_dg
            need_next_frame = True

        if tp and n > 0:
            outer = self._sector_outer()
            arcs = self._compute_sector_arcs()

            # 背景盘所有扇形同步展开/收起（整体进度，不跟随按钮递进）
            rr = max(0.0, self._sector_scale)
            aa = smoothstep(max(0.0, min(1.0, rr)))

            # 弧线场景：填充扇形补全整圆（360° − 有效扇形之和），随端部扇区缩放淡入
            filler = self._filler_arc()
            if filler:
                f0, f1, fsize = filler
                rr_f = rr
                aa_f = aa
                if rr_f > 0.005 and aa_f > 0.01:
                    r_f = outer * rr_f
                    fill_f = int(aa_f * 100)
                    if fill_f >= 1:
                        fpath = QPainterPath()
                        fpath.moveTo(cx, cy)
                        fpath.arcTo(QRectF(cx - r_f, cy - r_f, r_f * 2, r_f * 2),
                                    math.degrees(-f0), -math.degrees(fsize))
                        fpath.closeSubpath()
                        fgrad = QRadialGradient(cx, cy, r_f)
                        fgrad.setColorAt(0.0, QColor(42, 46, 62, 0))
                        fgrad.setColorAt(1.0, QColor(42, 46, 62, fill_f))
                        p.setBrush(fgrad)
                        p.setPen(Qt.NoPen)
                        p.drawPath(fpath)

            # 绘制扇形：悬停的最后一个画，避免被两侧未高亮扇形遮挡
            order = list(range(n))
            if 0 <= self._hovered_sector < n:
                order.remove(self._hovered_sector)
                order.append(self._hovered_sector)
            for i in order:
                if aa < 0.01 or rr < 0.005:
                    continue
                a0, a1 = arcs[i]
                span = (a1 - a0) % (2 * math.pi)
                if span < 0.001 or span > 2 * math.pi - 0.001:
                    span = 2 * math.pi
                glow = self._sector_glow.get(i, 0.0)
                r = outer * rr * (1.0 + 0.05 * glow)  # 悬停整体放大 5%（半径+角度）随 glow 渐变
                if glow > 0.01:
                    a0_draw = a0 - span * 0.025 * glow
                    span_draw = span * (1.0 + 0.05 * glow)
                else:
                    a0_draw, span_draw = a0, span
                fill_a = int(aa * 100)  # 背景盘整体透明度减半
                if fill_a < 1:
                    continue
                if glow > 0.01:
                    # 悬停：径向渐变高光，中心透明、向外提亮
                    base = (42, 46, 62)
                    edge = (128, 176, 246)
                    c0 = tuple(int(base[k] + (edge[k] - base[k]) * glow * 0.30) for k in range(3))
                    c1 = tuple(int(base[k] + (edge[k] - base[k]) * glow) for k in range(3))
                    grad = QRadialGradient(cx, cy, r)
                    grad.setColorAt(0.0, QColor(c0[0], c0[1], c0[2], 0))
                    grad.setColorAt(0.55, QColor(c0[0], c0[1], c0[2], int(fill_a * 0.35)))
                    grad.setColorAt(1.0, QColor(c1[0], c1[1], c1[2], fill_a))
                    p.setBrush(grad)
                else:
                    # 中心透明 → 外缘不透明渐变
                    grad = QRadialGradient(cx, cy, r)
                    grad.setColorAt(0.0, QColor(42, 46, 62, 0))
                    grad.setColorAt(0.55, QColor(42, 46, 62, int(fill_a * 0.35)))
                    grad.setColorAt(1.0, QColor(42, 46, 62, fill_a))
                    p.setBrush(grad)
                p.setPen(Qt.NoPen)
                if span_draw >= 2 * math.pi - 0.01:
                    p.drawEllipse(QPointF(cx, cy), r, r)
                else:
                    path = QPainterPath()
                    path.moveTo(cx, cy)
                    # Qt 弧线角度与按钮坐标（y 向下）相反，取镜像角度绘制
                    path.arcTo(QRectF(cx - r, cy - r, r * 2, r * 2),
                               math.degrees(-a0_draw), -math.degrees(span_draw))
                    path.closeSubpath()
                    p.drawPath(path)

            # 拖入背景盘：有空间时整盘高亮闪烁（新增模式）；满 8 个时只做分区高亮
            if self._drop_glow > 0.01 and rr > 0.01 and len(self.buttons) < 8:
                pulse = self._glow_pulse()
                glow_a = int(60 * self._drop_glow * pulse)
                if glow_a >= 1:
                    gg = QRadialGradient(cx, cy, outer * rr)
                    gg.setColorAt(0.0, QColor(110, 200, 255, 0))
                    gg.setColorAt(1.0, QColor(110, 200, 255, glow_a))
                    p.setBrush(gg)
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QPointF(cx, cy), outer * rr, outer * rr)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(110, 200, 255, int(210 * self._drop_glow * pulse)), 2))
                p.drawEllipse(QPointF(cx, cy), outer * rr, outer * rr)

            # 分隔线：只保留中段一截，随两侧扇区一起缩放淡出
            if n >= 2:
                bounds = [arcs[i][0] for i in range(n)]
                if (self._arc_sweep < 2 * math.pi - 0.01
                        and getattr(self, '_fan_e0', None) is not None):
                    # 空分区顺时针侧的另一条分隔线（两条线夹出空分区）
                    bounds.append(self._fan_e0 + self._fan_free)
                for ba in bounds:
                    rr_b = rr
                    aa_b = aa
                    if rr_b < 0.01 or aa_b < 0.01:
                        continue
                    r_lo = outer * 0.52 * rr_b
                    r_hi = outer * 0.78 * rr_b
                    x1 = cx + r_lo * math.cos(ba)
                    y1 = cy + r_lo * math.sin(ba)
                    x2 = cx + r_hi * math.cos(ba)
                    y2 = cy + r_hi * math.sin(ba)
                    p.setPen(QPen(QColor(255, 255, 255, int(aa_b * 20)), 1))
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                p.setPen(Qt.NoPen)

        # 空菜单：只画背景盘与"拖入添加"提示
        if self._empty_menu:
            rr = max(0.0, self._sector_scale)
            aa = smoothstep(max(0.0, min(1.0, rr)))
            if aa > 0.01:
                ps = self.pet.pet_size
                outer = (ps * 0.55 + self._icon_size * 0.8 + self._icon_size * 0.55 + 6) * rr
                grad = QRadialGradient(cx, cy, outer)
                grad.setColorAt(0.0, QColor(42, 46, 62, 0))
                grad.setColorAt(1.0, QColor(42, 46, 62, int(aa * 100)))
                p.setBrush(grad)
                p.setPen(QPen(QColor(255, 255, 255, int(aa * 20)), 1))
                p.drawEllipse(QPointF(cx, cy), outer, outer)
                if self._drop_glow > 0.01:
                    pulse = self._glow_pulse()
                    glow_a = int(60 * self._drop_glow * pulse)
                    if glow_a >= 1:
                        gg = QRadialGradient(cx, cy, outer)
                        gg.setColorAt(0.0, QColor(110, 200, 255, 0))
                        gg.setColorAt(1.0, QColor(110, 200, 255, glow_a))
                        p.setBrush(gg)
                        p.setPen(Qt.NoPen)
                        p.drawEllipse(QPointF(cx, cy), outer, outer)
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(110, 200, 255, int(190 * self._drop_glow * pulse)), 2))
                    p.drawEllipse(QPointF(cx, cy), outer, outer)
                if aa > 0.4:
                    # 提示文字放在盘内下半部分（贴边时自动移到可见空位）
                    tx, ty = self._empty_hint_pos(outer)
                    p.setPen(QColor(195, 205, 220, int(aa * 130)))
                    p.setFont(QFont("SimHei", 9))
                    p.drawText(QRectF(tx - outer, ty - 9, outer * 2, 18),
                               Qt.AlignCenter, "拖入添加")

        # 拖拽重排：交换目标用分区高亮（_hovered_sector），删除提示用气泡（Toast）

        # ── 绘制按钮（从 _btn_anim_state 驱动，与呼吸动画同源） ──
        fs = self._icon_size
        now = time.monotonic()
        # 贴边自适应排列时按钮会相互靠近：按链的方向（从空分区顺时针侧起）分层绘制，
        # 后画的在上层，避免"索引靠后的按钮（如左上）"随机置顶遮挡其他按钮；
        # 悬停按钮始终最后画，保证放大时不被邻居盖住。
        draw_order = list(range(len(self.buttons)))
        if self._fan_e0 is not None and len(self.buttons) > 1:
            cx0, cy0 = self._center_pos.x(), self._center_pos.y()
            e0 = self._fan_e0

            def _chain_angle(i):
                st = self._btn_anim_state.get(i)
                if not st:
                    return 0.0
                return (math.atan2(st[1] - cy0, st[0] - cx0) - e0) % (2 * math.pi)

            draw_order.sort(key=_chain_angle)
        hover = self._hovered_btn if self._hovered_btn >= 0 else self._hovered_sector
        if 0 <= hover < len(draw_order):
            draw_order.remove(hover)
            draw_order.append(hover)
        for i in draw_order:
            st = self._btn_anim_state.get(i)
            if not st:
                continue
            x, y, scale_ratio, alpha = st
            if alpha < 0.01 or scale_ratio < 0.005:
                continue
            # 拖拽中的按钮跟随鼠标（绘制位置夹紧在窗口内，避免窗口外绘制导致崩溃）
            if i == self._reorder_drag and self._reorder_local is not None:
                half = fs / 2.0 + 2
                x = max(half, min(self.width() - half, self._reorder_local.x()))
                y = max(half, min(self.height() - half, self._reorder_local.y()))
                alpha = max(alpha, 0.9)
            # 计算悬停缩放（完全由 paintEvent 时间驱动，不依赖定时器）
            ts = self._btn_hover_ts.get(i, 0)
            hover_s = 1.0
            if ts > 0:
                # enter 动画中
                ht = now - ts
                if ht < self._HOVER_ENTER_DUR:
                    hover_s = 1.0 + (self._HOVER_SCALE - 1.0) * ease_out_cubic(ht / self._HOVER_ENTER_DUR)
                    need_next_frame = True
                else:
                    hover_s = self._HOVER_SCALE
            elif ts < 0:
                # exit 动画中
                ht = now + ts  # ts 是负数，now - |ts| = now + ts
                if ht < self._HOVER_EXIT_DUR:
                    hover_s = self._HOVER_SCALE - (self._HOVER_SCALE - 1.0) * ease_out_cubic(ht / self._HOVER_EXIT_DUR)
                    need_next_frame = True
                else:
                    hover_s = 1.0
                    self._btn_hover_ts[i] = 0  # 动画完成，清除
            final_scale = scale_ratio * hover_s
            p.save()
            p.setOpacity(alpha)
            p.translate(x, y)
            if abs(final_scale - 1.0) > 0.002:
                p.scale(final_scale, final_scale)
            # 背景
            p.setBrush(QColor(255, 255, 255, 220))
            p.setPen(QPen(QColor(200, 210, 230, 180), 1))
            r = fs / 2.0
            p.drawRoundedRect(QRectF(-r, -r, fs, fs), 5, 5)
            # 图标
            pm = self._btn_pixmaps[i] if i < len(self._btn_pixmaps) else None
            if pm and not pm.isNull():
                icon_r = max(4, int(fs - 4))
                scaled = pm.scaled(icon_r, icon_r, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(int(-scaled.width() / 2), int(-scaled.height() / 2), scaled)
            p.restore()
        if self._drop_glow > 0.01:
            need_next_frame = True
        p.end()
        # 悬停动画未结束时持续触发重绘
        if need_next_frame:
            QTimer.singleShot(16, self.update)

    def _find_btn_at(self, pos):
        """命中检测：以按钮背景圆角正方形区域为准，外扩 2px 防止边缘抖动"""
        fs = self._icon_size
        half = fs / 2.0 + 2
        px, py = pos.x(), pos.y()
        for i, (tx, ty, scale, alpha) in self._btn_anim_state.items():
            if alpha < 0.3 or scale < 0.3:
                continue
            if abs(px - tx) <= half and abs(py - ty) <= half:
                return i
        return -1

    def _pet_circle_contains(self, pos):
        """桌宠图标圆形区域（与预览一致：比图标大 48.5%），用于右键分发。"""
        try:
            ps = float(getattr(self.pet, "pet_size", 75) or 75)
        except Exception:
            ps = 75.0
        r = ps / 2.0 * 1.485
        dx = pos.x() - self._center_pos.x()
        dy = pos.y() - self._center_pos.y()
        return dx * dx + dy * dy <= r * r

    def _sector_outer(self):
        """扇形外沿半径（与绘制一致）"""
        cx = self._center_pos.x()
        cy = self._center_pos.y()
        radius = 0.0
        for tx, ty in self._target_positions:
            radius = max(radius, math.hypot(tx - cx, ty - cy))
        if radius <= 0 and self._empty_menu:
            ps = self.pet.pet_size
            return (ps * 0.55 + self._icon_size * 0.8 + self._icon_size * 0.55 + 6) * max(0.0, self._sector_scale)
        return radius + self._icon_size * 0.55 + 6

    def _sector_outer_full(self):
        """扇形外沿最终半径（不随展开动画缩放），供气泡避让定位用：
        用稳定半径，避免空菜单展开动画期间气泡目标来回跳（先向中间跳再回去）。"""
        cx = self._center_pos.x()
        cy = self._center_pos.y()
        radius = 0.0
        for tx, ty in self._target_positions:
            radius = max(radius, math.hypot(tx - cx, ty - cy))
        if radius <= 0 and self._empty_menu:
            ps = self.pet.pet_size
            return ps * 0.55 + self._icon_size * 0.8 + self._icon_size * 0.55 + 6
        return radius + self._icon_size * 0.55 + 6

    def _compute_sector_arcs(self):
        """每个按钮对应的扇形角度区间：整圆环绕均分；贴边时为空分区两侧均匀分区"""
        n = len(self.buttons)
        cx = self._center_pos.x()
        cy = self._center_pos.y()
        tp = self._target_positions
        if n == 1:
            return [(0.0, 2 * math.pi)]
        angles = [math.atan2(ty - cy, tx - cx) for tx, ty in tp]
        if self._arc_sweep >= 2 * math.pi - 0.01:
            arcs = []
            for i in range(n):
                prev = (i - 1) % n
                nxt = (i + 1) % n
                arcs.append((_mid_angle(angles[prev], angles[i]),
                             _mid_angle(angles[i], angles[nxt])))
            return arcs
        # 贴边：空分区（两条分隔线之间）之外的夹角均匀分给按钮分区
        E0 = getattr(self, '_fan_e0', None)
        F = getattr(self, '_fan_free', 2 * math.pi)
        slot0 = getattr(self, '_fan_slot0', 0)
        if E0 is None:
            # 兜底：按当前按钮角度均分
            gap = self._arc_sweep / n
            return [(a - gap * 0.5, a + gap * 0.5) for a in angles]
        arcs = [0.0] * n
        for k in range(n):
            m = (k - slot0) % n
            arcs[k] = (E0 + F * m / n, E0 + F * (m + 1) / n)
        return arcs

    def _filler_arc(self):
        """贴边时补全整圆：填充空分区（360° − 按钮分区总夹角）"""
        n = len(self.buttons)
        if n < 2 or self._arc_sweep >= 2 * math.pi - 0.01:
            return None
        E0 = getattr(self, '_fan_e0', None)
        F = getattr(self, '_fan_free', 2 * math.pi)
        if E0 is None:
            cx = self._center_pos.x()
            cy = self._center_pos.y()
            angles = [math.atan2(ty - cy, tx - cx) for tx, ty in self._target_positions]
            gap = self._arc_sweep / n
            start = angles[-1] + gap * 0.5
            end = angles[0] - gap * 0.5 + 2 * math.pi
            size = (end - start) % (2 * math.pi)
            return (start, end, size) if size >= 0.01 else None
        start = E0 + F
        end = E0 + 2 * math.pi
        size = end - start
        if size < 0.01:
            return None
        return (start, end, size)

    def _empty_hint_pos(self, outer):
        """空菜单提示文字位置：默认盘内下半部分；贴边时自动选可见空位"""
        cx, cy = self._center_pos.x(), self._center_pos.y()
        screen = QApplication.primaryScreen().availableGeometry()
        pg = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.height() // 2))
        px, py = pg.x(), pg.y()

        def _visible(a):
            ex = px + outer * math.cos(a)
            ey = py + outer * math.sin(a)
            return (screen.left() - 4 <= ex <= screen.right() + 4
                    and screen.top() - 4 <= ey <= screen.bottom() + 4)

        if _visible(math.pi / 2):
            return (cx, cy + outer * 0.60)
        for k in range(72):
            a = math.pi / 2 + 2 * math.pi * (k + 1) / 72
            if _visible(a):
                return (cx + outer * 0.60 * math.cos(a), cy + outer * 0.60 * math.sin(a))
        return (cx, cy + outer * 0.60)

    def _glow_pulse(self):
        """待修改状态高光：每秒两下的呼吸闪烁（0.35..1.0）"""
        return 0.35 + 0.65 * (0.5 + 0.5 * math.sin(time.monotonic() * 4 * math.pi))

    def _find_sector_at(self, pos):
        """命中检测：鼠标所在的扇形区域（按最终几何，含悬停外延 5%）"""
        if not self.buttons or not self.is_visible_state:
            return -1
        cx = self._center_pos.x()
        cy = self._center_pos.y()
        dx = pos.x() - cx
        dy = pos.y() - cy
        r = math.hypot(dx, dy)
        if r < 1:
            return -1
        a = math.atan2(dy, dx)
        outer = self._sector_outer()
        if r > outer * 1.05:
            return -1
        arcs = self._compute_sector_arcs()
        for i, (a0, a1) in enumerate(arcs):
            span = (a1 - a0) % (2 * math.pi)
            rel = (a - a0) % (2 * math.pi)
            # 悬停放大 5% 后角度也外扩，命中区相应放宽
            if rel <= span * 1.05:
                return i
        return -1

    def mousePressEvent(self, event):
        # 保持桌宠始终在背景盘上层
        self.pet.raise_()
        if event.button() == Qt.LeftButton:
            i = self._find_btn_at(event.pos())
            si = self._find_sector_at(event.pos())
            if si >= 0:
                # 点击背景盘时保持高亮
                self._hovered_sector = si
            if i >= 0 and self.is_visible_state and not self._animating:
                # 记录待拖拽按钮：拖拽则交换/删除，未拖拽则松手启动
                self._reorder_btn = i
                self._reorder_press = event.globalPos()
                self._reorder_drag = -1
                self._reorder_target = -1
                self._reorder_outside = False
                return
            # 点击背景盘区域（含填充扇形）不收回菜单，只有点击盘外空白才收回
            if self.is_visible_state and not self._animating:
                if si < 0:
                    dx = event.pos().x() - self._center_pos.x()
                    dy = event.pos().y() - self._center_pos.y()
                    if math.hypot(dx, dy) > self._sector_outer() * 1.05:
                        self.hide_menu()
        elif event.button() == Qt.RightButton:
            if self._pet_circle_contains(event.pos()):
                # 径向菜单窗口始终置顶，桌宠图标被盖住：右键到桌宠区域时弹桌宠本体菜单
                self.pet.show_pet_menu(event.globalPos())
                return
            i = self._find_btn_at(event.pos())
            if i >= 0 and self.is_visible_state and not self._animating:
                self._btn_context_menu(event.globalPos(), i)
            elif self.is_visible_state and not self._animating:
                # 盘面空白处右键：直接添加网页链接按钮
                dx = event.pos().x() - self._center_pos.x()
                dy = event.pos().y() - self._center_pos.y()
                if math.hypot(dx, dy) <= self._sector_outer() * 1.05:
                    self._empty_context_menu(event.globalPos())

    def mouseMoveEvent(self, event):
        # 拖拽重排：按下按钮后移动超过阈值进入交换/删除模式
        if self._reorder_btn >= 0 and not self._animating:
            if self._reorder_drag < 0:
                d = event.globalPos() - self._reorder_press
                if abs(d.x()) > 6 or abs(d.y()) > 6:
                    self._reorder_drag = self._reorder_btn
                    self._tip.hide()
                    self._hovered_btn = -1
                    self._btn_hover_ts.clear()
            if self._reorder_drag >= 0:
                # 窗口自展开起已预留拖拽空间（_drag_room），拖拽中不改窗口几何
                self._reorder_local = event.pos()
                lp = self._reorder_local
                cx = self._center_pos.x()
                cy = self._center_pos.y()
                dist = math.hypot(lp.x() - cx, lp.y() - cy)
                outside = (dist > self._sector_outer() + 8)
                self._reorder_outside = outside
                # 拖到的分区即交换目标（与鼠标经过分区一致，无按钮边缘高亮）
                si = self._find_sector_at(lp)
                self._reorder_target = si
                if si != self._hovered_sector:
                    self._hovered_sector = si
                    self.update()
                # 提示气泡：盘外=删除，按钮上=交换
                if outside:
                    hint = "松手删除"
                elif self._reorder_target >= 0 and self._reorder_target != self._reorder_drag:
                    hint = "松手交换"
                else:
                    hint = None
                if hint != self._reorder_toast_text:
                    self._close_reorder_toast()
                    self._reorder_toast_text = hint
                    if hint:
                        self._reorder_toast = Toast(hint, event.globalPos(), duration=0)
                self.update()
                return
        i = self._find_btn_at(event.pos())
        si = self._find_sector_at(event.pos())
        if si != self._hovered_sector:
            self._hovered_sector = si
            self.update()
        if i >= 0 and self.is_visible_state and not self._animating:
            self._start_hover_anim(i)
            if self.pet.settings.get("show_tooltips", True):
                if i < len(self._btn_names) and self._btn_names[i]:
                    self._tip.setText(self._btn_names[i])
                    tp = self._target_positions[i] if i < len(self._target_positions) else None
                    if tp:
                        bg = self.mapToGlobal(QPoint(int(tp[0]), int(tp[1])))
                        self._tip.move(bg.x() - self._tip.width() // 2,
                                       bg.y() - self._tip.height() - 8)
                        self._tip.show()
        else:
            if self._hovered_btn >= 0:
                self._stop_hover_anim()
            self._tip.hide()

    def leaveEvent(self, event):
        if self._hovered_btn >= 0:
            self._stop_hover_anim()
        if self._hovered_sector >= 0:
            self._hovered_sector = -1
            self.update()
        self._tip.hide()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # 使用 MoveAction 去掉系统默认的 "+复制" 光标标签
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        event.setDropAction(Qt.MoveAction)
        event.accept()
        # 悬停背景盘：外缘高光（平滑过渡）
        dx = event.pos().x() - self._center_pos.x()
        dy = event.pos().y() - self._center_pos.y()
        on_disk = math.hypot(dx, dy) <= self._sector_outer() + 8
        if on_disk != self._drop_over_disk:
            self._drop_over_disk = on_disk
            self.update()
        # 拖入高亮：有空间=整盘闪烁；满 8 个=分区高亮（与鼠标经过分区一致）
        full = len(self.buttons) >= 8
        si = self._find_sector_at(event.pos()) if full else -1
        if si != self._hovered_sector:
            self._hovered_sector = si
            self.update()

    def dragLeaveEvent(self, event):
        self._drop_over_disk = False
        if self._hovered_sector >= 0:
            self._hovered_sector = -1
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_over_disk = False
        if self._hovered_sector >= 0:
            self._hovered_sector = -1
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        path = None
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and (os.path.isfile(p) or os.path.isdir(p)):
                path = p
                break
            u = url.url()
            if u.startswith(("http://", "https://")):
                path = u
                break
        if not path:
            Toast("不支持的格式", self.mapToGlobal(event.pos()))
            event.ignore()
            return
        if path.startswith(("http://", "https://")):
            name = path.split("//")[-1].split("/")[0]
        else:
            name = os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)
        slots = list(self.pet.settings.get("slot_shortcuts", []))
        # 满 8 个时以分区为替换目标，否则以按钮为目标（空盘处=新增）
        if len(slots) >= 8:
            i = self._find_sector_at(event.pos())
        else:
            i = self._find_btn_at(event.pos())
        sc = {"name": name, "path": path}
        # 兼容格式直接添加/替换，不再弹确认（只有替换桌宠图标才需要确认）
        self._pending_drop = (slots, i, sc)
        QTimer.singleShot(0, self._process_pending_drop)
        event.acceptProposedAction()

    def _process_pending_drop(self):
        pending = getattr(self, '_pending_drop', None)
        if not pending:
            return
        self._pending_drop = None
        slots, i, sc = pending
        if 0 <= i < len(slots):
            slots[i] = sc
        elif len(slots) < 8:
            slots.append(sc)
        self.pet.settings["slot_shortcuts"] = slots
        save_settings(self.pet.settings)
        self.rearrange(slots)  # 自适应重新排列（带过渡动画）

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._reorder_btn >= 0:
            src = self._reorder_btn
            if self._reorder_drag >= 0:
                slots = list(self.pet.settings.get("slot_shortcuts", []))
                if self._reorder_outside and 0 <= src < len(slots):
                    slots.pop(src)
                    self.pet.settings["slot_shortcuts"] = slots
                    save_settings(self.pet.settings)
                    self.rearrange(slots)
                elif 0 <= self._reorder_target < len(slots) and self._reorder_target != src:
                    slots[src], slots[self._reorder_target] = slots[self._reorder_target], slots[src]
                    self.pet.settings["slot_shortcuts"] = slots
                    save_settings(self.pet.settings)
                    self.rearrange(slots)
                else:
                    self.update()
            else:
                # 普通点击：启动该按钮
                sc = self._btn_shortcuts[src] if src < len(self._btn_shortcuts) else None
                if sc:
                    self._launch(sc.get("path", ""))
            self._reorder_btn = -1
            self._reorder_drag = -1
            self._reorder_target = -1
            self._reorder_press = None
            self._reorder_local = None
            self._reorder_outside = False
            self._close_reorder_toast()
            self._hovered_sector = -1
            self.update()
            return
        self._reorder_btn = -1
        self._reorder_drag = -1
        self._reorder_target = -1
        self._reorder_press = None
        self._reorder_local = None
        self._reorder_outside = False
        self._close_reorder_toast()
        self._hovered_sector = -1
        self.update()


def _make_pixel_halo(pm):
    """沿图案 alpha 像素边缘外扩 ±3px 的高光描边（对称）"""
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    mask = QImage(img.size(), QImage.Format_ARGB32)
    mask.fill(Qt.transparent)
    mp = QPainter(mask)
    mp.setCompositionMode(QPainter.CompositionMode_Source)
    mp.drawImage(0, 0, img)
    mp.setCompositionMode(QPainter.CompositionMode_SourceIn)
    mp.fillRect(mask.rect(), QColor(255, 255, 255))
    mp.end()
    halo = QImage(mask.size(), QImage.Format_ARGB32)
    halo.fill(Qt.transparent)
    hp = QPainter(halo)
    # 关键：SourceOver 累积各方向偏移，否则后一次绘制会覆盖前一次（高光偏右下）
    hp.setCompositionMode(QPainter.CompositionMode_SourceOver)
    for dx in (-3, -2, -1, 0, 1, 2, 3):
        for dy in (-3, -2, -1, 0, 1, 2, 3):
            if dx == 0 and dy == 0:
                continue
            hp.drawImage(dx, dy, mask)
    hp.setCompositionMode(QPainter.CompositionMode_SourceIn)
    hp.fillRect(halo.rect(), QColor(150, 225, 255))
    hp.end()
    return QPixmap.fromImage(halo)


# ==================== 预览组件 ====================
class PreviewWidget(QWidget):
    shortcuts_changed = pyqtSignal()
    pet_image_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(360)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.pet_pixmap = None
        self._pet_image_path = ""
        self._gif_movie = None
        self._gif_frame_pm = None
        self._webp_anim = None
        self._webp_frame_pm = None
        self._pet_pm_scaled = None
        self._pet_image_phys_path = ""
        self._pet_rect = QRect()
        self.slot_shortcuts = []
        self._pet_size = 85
        self._layout_dirty = True
        self._layout_cx = 0
        self._layout_cy = 0
        self._layout_ps = 0
        self._layout_radius = 0
        self._slot_size = 36
        self._disk_outer = 0
        self._pet_opacity = 1.0
        self._slot_rects = []
        self._hover_slot = -1
        self._drag_source = self._drag_target = -1
        self._press_pos = None
        self._is_dragging = False
        self._ext_hover = -1
        self._drag_outside = False
        self._ext_hover_pet = False
        self._ext_drag_active = False  # 外部拖入预览进行中
        # 预览桌宠图标的呼吸待机动画
        self._breath_timer = QElapsedTimer()
        self._breath_timer.start()
        self._preview_anim = QTimer(self)
        self._preview_anim.timeout.connect(self._tick_preview)
        self._preview_anim.start(16)
        # 与悬浮径向菜单一致的交互状态
        self._hover_sector = -1
        self._sector_glow = {}
        self._glow_ts = 0.0
        self._drop_over_disk = False
        self._drop_glow = 0.0
        self._drop_glow_ts = 0.0
        self._pet_halo_pm = None
        self._pet_halo_key = None
        # 插槽重排动画（添加/删除/替换/交换，与悬浮菜单过渡一致）
        self._slot_anim_active = False
        self._slot_anim_t0 = 0.0
        self._slot_anim_dur = 0.30
        self._slot_anim_from = []
        self._slot_anim_to = []
        self._slot_anim_new = set()
        self._anim_centers = []
        self._btn_scale = {}
        self._drag_pos = None
        # 自定义标签（与径向菜单标签同款圆角样式）
        self._tip_label = _TipLabel()
        self._tip_label.hide()
        # 插槽增删/交换后重新排列布局
        self.shortcuts_changed.connect(lambda: setattr(self, '_layout_dirty', True))

    def set_pet_image_path(self, path):
        self._pet_image_path = path or ""

    def set_pet_preview_size(self, size):
        self._pet_size = size
        self._layout_dirty = True
        self._refresh_static_pm()
        self._refresh_gif_frame()
        self.update()

    def set_slot_size(self, s):
        self._slot_size = s
        self._layout_dirty = True
        self.update()

    def set_pet_opacity(self, opacity):
        self._pet_opacity = opacity
        self.update()

    def set_pet_pixmap(self, pm):
        self.pet_pixmap = pm
        self._refresh_static_pm()
        self.update()

    def load_pet_image(self, path):
        """加载桌宠图片：.webp 用 Pillow 动图，.gif 用 QMovie，其它用 QPixmap。"""
        if self._gif_movie is not None:
            self._gif_movie.stop()
            self._gif_movie = None
        if self._webp_anim is not None:
            self._webp_anim.stop()
            self._webp_anim = None
        self._pet_image_phys_path = path or ""
        self.pet_pixmap = None
        self._gif_frame_pm = None
        self._webp_frame_pm = None
        if not path or not os.path.exists(path):
            self._refresh_static_pm()
            self.update()
            return
        ext = os.path.splitext(path)[1].lower()
        if ext == ".webp":
            anim = _WebpAnim(path, self._pet_size, self._on_webp_frame, self)
            if anim.is_valid():
                self._webp_anim = anim
                self._webp_frame_pm = None
                anim.start()
                self.update()
                return
        if ext == ".gif":
            movie = QMovie(path)
            if movie.isValid():
                movie.frameChanged.connect(self._on_gif_frame)
                self._gif_movie = movie
                self._gif_frame_pm = None
                movie.start()
            else:
                # GIF 不合法回退到静态
                pm = QPixmap(path)
                if not pm.isNull():
                    self.pet_pixmap = pm
                    self._refresh_static_pm()
        else:
            pm = QPixmap(path)
            if not pm.isNull():
                self.pet_pixmap = pm
                self._refresh_static_pm()
        self.update()

    def _refresh_static_pm(self):
        if self.pet_pixmap and not self.pet_pixmap.isNull():
            self._pet_pm_scaled = self.pet_pixmap.scaled(self._pet_size, self._pet_size,
                                                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self._pet_pm_scaled = None

    def _refresh_gif_frame(self):
        if self._gif_movie is not None:
            raw = self._gif_movie.currentPixmap()
            if raw and not raw.isNull():
                self._gif_frame_pm = raw.scaled(self._pet_size, self._pet_size,
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _on_gif_frame(self):
        self._refresh_gif_frame()
        self.update()

    def _on_webp_frame(self):
        if self._webp_anim is not None:
            self._webp_frame_pm = self._webp_anim.current_pixmap()
        self.update()

    def set_slots(self, sc):
        if not self._slot_rects:
            # 首次填充/清空后：直接呈现正确排列，不做重排动画
            self.slot_shortcuts = list(sc[:8])
            self._layout_dirty = True
            self.update()
            return
        old_pos, old_list = self._slot_positions()
        self.slot_shortcuts = list(sc[:8])
        self._layout_dirty = True
        self._ensure_layout()
        self._start_slot_anim(old_pos, old_list)
        self.update()

    def _calc_layout(self):
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        ps = self._pet_size
        s = self._slot_size
        radius = (ps * 0.86 + s)  # 按钮往内移一点，离菜单盘外边缘更远
        n = len(self.slot_shortcuts)
        pad = s // 2 + 12
        max_r = min(cx - pad, cy - pad)
        if max_r < radius:
            radius = max(max(60, s), max_r - 6)
        self._disk_outer = radius + s * 0.7 + 8
        if self._disk_outer > max_r + s // 2:
            self._disk_outer = max_r + s // 2
        if n > 0:
            angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
            self._slot_rects = [QRect(int(cx + radius * math.cos(a) - s // 2),
                                       int(cy + radius * math.sin(a) - s // 2), s, s) for a in angles]
        else:
            self._slot_rects = []
        self._pet_rect = QRect(cx - ps // 2, cy - ps // 2, ps, ps)
        self._layout_cx = cx
        self._layout_cy = cy
        self._layout_ps = ps
        self._layout_radius = radius
        self._layout_dirty = False

    def _ensure_layout(self):
        if self._layout_dirty:
            self._calc_layout()
        return self._layout_cx, self._layout_cy, self._layout_ps, self._layout_radius

    def resizeEvent(self, event):
        self._layout_dirty = True
        super().resizeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(27, 33, 48))
        p.setBrush(QColor(35, 42, 58))
        p.setPen(QPen(QColor(80, 92, 116), 1))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)
        if self._ext_hover == -2:
            p.setPen(QPen(QColor(60, 160, 80), 3))
            p.setBrush(QColor(140, 220, 160, 40))
            p.drawRoundedRect(2, 2, w - 5, h - 5, 8, 8)

        cx, cy, ps, radius = self._ensure_layout()
        n = len(self.slot_shortcuts)
        disk_r = self._disk_outer

        # 插槽平滑重排动画（与悬浮菜单过渡一致）：按钮从旧位置滑向新位置
        if self._slot_anim_active and n > 0:
            k = ease_out_cubic(min(1.0, (time.monotonic() - self._slot_anim_t0) / self._slot_anim_dur))
            centers = []
            for i in range(n):
                f = self._slot_anim_from[i]
                t = self._slot_anim_to[i]
                centers.append(QPointF(f.x() + (t.x() - f.x()) * k, f.y() + (t.y() - f.y()) * k))
            if k >= 1.0:
                self._slot_anim_active = False
                centers = [QPointF(r.center()) for r in self._slot_rects]
            self._anim_centers = centers
        else:
            self._anim_centers = [QPointF(r.center()) for r in self._slot_rects]

        # 交互状态过渡（时间驱动，与悬浮径向菜单一致）
        now_t = time.monotonic()
        dt_g = min(0.05, max(0.0, now_t - self._glow_ts)) if self._glow_ts else 0.0
        self._glow_ts = now_t
        if dt_g > 0:
            for i in range(n):
                target = 1.0 if i == self._hover_sector else 0.0
                cur = self._sector_glow.get(i, 0.0)
                if abs(cur - target) > 0.001:
                    rate = 12.0 if target > cur else 6.0
                    cur += (target - cur) * min(1.0, dt_g * rate)
                    if abs(cur - target) < 0.001:
                        cur = target
                    self._sector_glow[i] = cur
        targ_dg = 1.0 if (self._ext_drag_active and self._drop_over_disk and n < 8) else 0.0
        if abs(self._drop_glow - targ_dg) > 0.001:
            rate_dg = 8.0 if targ_dg > self._drop_glow else 5.0
            self._drop_glow += (targ_dg - self._drop_glow) * min(1.0, dt_g * rate_dg)
            if abs(self._drop_glow - targ_dg) < 0.001:
                self._drop_glow = targ_dg

        # 背景盘：中心透明、向外渐变（与悬浮径向菜单一致）
        disk_grad = QRadialGradient(cx, cy, disk_r)
        disk_grad.setColorAt(0.0, QColor(42, 46, 62, 0))
        disk_grad.setColorAt(0.6, QColor(42, 46, 62, 70))
        disk_grad.setColorAt(1.0, QColor(42, 46, 62, 130))
        p.setBrush(disk_grad)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), disk_r, disk_r)

        # 扇形分区（与悬浮径向菜单一致：整圆均分 + 悬停/拖入高光）
        if n > 0:
            angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
            arcs = []
            for i in range(n):
                prev = (i - 1) % n
                nxt = (i + 1) % n
                arcs.append((_mid_angle(angles[prev], angles[i]),
                             _mid_angle(angles[i], angles[nxt])))
            order = list(range(n))
            if 0 <= self._hover_sector < n:
                order.remove(self._hover_sector)
                order.append(self._hover_sector)
            for i in order:
                a0, a1 = arcs[i]
                span = (a1 - a0) % (2 * math.pi)
                glow = self._sector_glow.get(i, 0.0)
                if glow > 0.01:
                    base = (42, 46, 62)
                    edge = (128, 176, 246)
                    c0 = tuple(int(base[k] + (edge[k] - base[k]) * glow * 0.30) for k in range(3))
                    c1 = tuple(int(base[k] + (edge[k] - base[k]) * glow) for k in range(3))
                    grad = QRadialGradient(cx, cy, disk_r)
                    grad.setColorAt(0.0, QColor(c0[0], c0[1], c0[2], 0))
                    grad.setColorAt(0.55, QColor(c0[0], c0[1], c0[2], 45))
                    grad.setColorAt(1.0, QColor(c1[0], c1[1], c1[2], 130))
                    p.setBrush(grad)
                else:
                    grad = QRadialGradient(cx, cy, disk_r)
                    grad.setColorAt(0.0, QColor(42, 46, 62, 0))
                    grad.setColorAt(0.6, QColor(42, 46, 62, 70))
                    grad.setColorAt(1.0, QColor(42, 46, 62, 130))
                    p.setBrush(grad)
                p.setPen(Qt.NoPen)
                path = QPainterPath()
                path.moveTo(cx, cy)
                path.arcTo(QRectF(cx - disk_r, cy - disk_r, disk_r * 2, disk_r * 2),
                           math.degrees(-a0), -math.degrees(span))
                path.closeSubpath()
                p.drawPath(path)
            # 分隔线：中段一截（与悬浮菜单一致）
            if n >= 2:
                p.setPen(QPen(QColor(255, 255, 255, 20), 1))
                for i in range(n):
                    ba = arcs[i][0]
                    x1 = cx + disk_r * 0.52 * math.cos(ba)
                    y1 = cy + disk_r * 0.52 * math.sin(ba)
                    x2 = cx + disk_r * 0.78 * math.cos(ba)
                    y2 = cy + disk_r * 0.78 * math.sin(ba)
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                p.setPen(Qt.NoPen)
        else:
            # 空盘：外圈（与悬浮空菜单一致）
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), disk_r, disk_r)

        # 拖入背景盘：有空间时整盘高亮闪烁
        if self._drop_glow > 0.01:
            pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(time.monotonic() * 4 * math.pi))
            glow_a = int(60 * self._drop_glow * pulse)
            if glow_a >= 1:
                gg = QRadialGradient(cx, cy, disk_r)
                gg.setColorAt(0.0, QColor(110, 200, 255, 0))
                gg.setColorAt(1.0, QColor(110, 200, 255, glow_a))
                p.setBrush(gg)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(cx, cy), disk_r, disk_r)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(110, 200, 255, int(210 * self._drop_glow * pulse)), 2))
            p.drawEllipse(QPointF(cx, cy), disk_r, disk_r)

        # 桌宠图标（呼吸待机 + 拖入像素边缘高光，与悬浮状态一致）
        pet_pm = None
        if self._gif_movie is not None:
            pet_pm = self._gif_frame_pm
        if (pet_pm is None or pet_pm.isNull()) and self._webp_anim is not None:
            pet_pm = self._webp_frame_pm
        if (pet_pm is None or pet_pm.isNull()) and self._pet_pm_scaled and not self._pet_pm_scaled.isNull():
            pet_pm = self._pet_pm_scaled
        if pet_pm and not pet_pm.isNull():
            tb = self._breath_timer.elapsed() / 1000.0
            pb = (tb % BREATH_PERIOD) / BREATH_PERIOD
            bscale = 1.0 + BREATH_SCALE * (0.5 + 0.5 * math.sin(pb * math.pi * 2))
            p.setOpacity(max(0.05, min(1.0, self._pet_opacity)))
            p.save()
            p.translate(cx, cy)
            p.scale(bscale, bscale)
            if self._ext_hover_pet:
                halo = self._preview_pet_halo(pet_pm)
                pulse = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(time.monotonic() * 4 * math.pi))
                p.setOpacity(min(1.0, 0.95 * pulse))
                p.drawPixmap(-pet_pm.width() // 2, -pet_pm.height() // 2, halo)
                p.setOpacity(max(0.05, min(1.0, self._pet_opacity)))
            p.drawPixmap(-pet_pm.width() // 2, -pet_pm.height() // 2, pet_pm)
            p.restore()
            p.setOpacity(1.0)
        else:
            p.setBrush(QColor(100, 140, 200))
            p.setPen(Qt.NoPen)
            p.drawEllipse(cx - ps // 2, cy - ps // 2, ps, ps)
        # 桌宠图标外虚线圆：提示该区域可替换桌宠图片
        p.setPen(QPen(QColor(150, 175, 215, 170), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        _pr = self._pet_circle_radius()
        p.drawEllipse(QPointF(self._pet_rect.center().x(), self._pet_rect.center().y()), _pr, _pr)

        # 按钮（与悬浮菜单同款：白色圆角方块 + 图标；悬停缩放/拖拽跟随/无外框高亮）
        if n > 0:
            ss = self._slot_size
            for i in range(n):
                hovered = (i == self._hover_slot) and not (self._is_dragging and i == self._drag_source)
                target_s = 1.3 if hovered else 1.0
                cur_s = self._btn_scale.get(i, 1.0)
                if abs(cur_s - target_s) > 0.001:
                    rate_s = 12.0 if target_s > cur_s else 6.0
                    cur_s += (target_s - cur_s) * min(1.0, dt_g * rate_s)
                    if abs(cur_s - target_s) < 0.001:
                        cur_s = target_s
                    self._btn_scale[i] = cur_s
            for i in range(n):
                c = self._anim_centers[i] if i < len(self._anim_centers) else QPointF(self._slot_rects[i].center())
                if self._is_dragging and i == self._drag_source and self._drag_pos is not None:
                    c = QPointF(self._drag_pos)
                scale_b = self._btn_scale.get(i, 1.0)
                if self._slot_anim_active and i in self._slot_anim_new:
                    k = min(1.0, (time.monotonic() - self._slot_anim_t0) / self._slot_anim_dur)
                    scale_b *= max(0.01, ease_out_back(k))
                # 与悬浮状态一致：底板 + 图标整体从中心缩放
                p.save()
                p.translate(c.x(), c.y())
                if abs(scale_b - 1.0) > 0.002:
                    p.scale(scale_b, scale_b)
                p.setBrush(QColor(255, 255, 255, 220))
                p.setPen(QPen(QColor(200, 210, 230, 180), 1))
                p.drawRoundedRect(QRectF(-ss / 2.0, -ss / 2.0, ss, ss), 5, 5)
                if i < len(self.slot_shortcuts):
                    sc = self.slot_shortcuts[i]
                    icon_pm = None
                    ci = sc.get("icon", "")
                    if ci and os.path.exists(ci):
                        icon_pm = QPixmap(ci).scaled(ss - 8, ss - 8, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if not icon_pm or icon_pm.isNull():
                    icon_pm = extract_exe_icon(sc.get("path", ""), ss - 8)
                if (not icon_pm or icon_pm.isNull()) and \
                        str(sc.get("path", "")).lower().startswith(("http://", "https://")):
                    icon_pm = _make_globe_icon(ss - 8)
                if icon_pm and not icon_pm.isNull():
                    p.drawPixmap(int(-icon_pm.width() / 2), int(-icon_pm.height() / 2), icon_pm)
                else:
                    p.setPen(QColor(80, 80, 80))
                    p.setFont(QFont("SimHei", 11))
                    p.drawText(QRectF(-ss / 2.0, -ss / 2.0, ss, ss), Qt.AlignCenter, sc.get("name", "?")[:2])
                p.restore()
        else:
            # 空盘：只保留背景盘外圈，不再显示提示文字
            pass

        # 底部操作说明（保持精简）
        p.setPen(QColor(150, 150, 150))
        p.setFont(QFont("SimHei", 10))
        if self._is_dragging and self._drag_outside and 0 <= self._drag_source < n:
            p.setPen(QColor(200, 80, 80))
            p.setFont(QFont("SimHei", 10, QFont.Bold))
            p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "松手删除此快捷方式")
        elif self._is_dragging and self._drag_target >= 0 and self._drag_target != self._drag_source:
            p.setPen(QColor(60, 130, 90))
            p.setFont(QFont("SimHei", 10, QFont.Bold))
            p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "松手交换到该插槽")
        elif self._is_dragging:
            p.setPen(QColor(120, 120, 120))
            p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "移到插槽上交换 · 拖出盘外删除")
        elif self._ext_drag_active:
            if self._ext_hover_pet:
                p.setPen(QColor(60, 130, 90))
                p.setFont(QFont("SimHei", 10, QFont.Bold))
                p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "松手替换桌宠图片")
            elif n >= 8:
                p.setPen(QColor(60, 130, 90))
                p.setFont(QFont("SimHei", 10, QFont.Bold))
                p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "松手替换该分区")
            else:
                p.drawText(QRect(0, h - 24, w, 22), Qt.AlignCenter, "松手添加为新插槽")
        elif n == 0:
            p.drawText(QRect(0, h - 22, w, 20), Qt.AlignCenter, "从文件管理器拖入快捷方式或文件到此处创建插槽")
        elif n < 8:
            p.drawText(QRect(0, h - 22, w, 20), Qt.AlignCenter, f"已 {n}/8 个插槽 · 继续拖入可添加更多 · 拖出盘外删除")
        p.end()

    def _find_slot_at(self, pos):
        for i, r in enumerate(self._slot_rects):
            if r.contains(pos):
                return i
        return -1

    def _find_sector_at(self, pos):
        """鼠标所在分区（与悬浮径向菜单一致）"""
        n = len(self.slot_shortcuts)
        if n == 0:
            return -1
        cx, cy, _, _ = self._ensure_layout()
        dx, dy = pos.x() - cx, pos.y() - cy
        r = math.hypot(dx, dy)
        if r < 1 or r > self._disk_outer + 2:
            return -1
        a = math.atan2(dy, dx)
        angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
        for i in range(n):
            prev = (i - 1) % n
            nxt = (i + 1) % n
            a0 = _mid_angle(angles[prev], angles[i])
            a1 = _mid_angle(angles[i], angles[nxt])
            span = (a1 - a0) % (2 * math.pi)
            rel = (a - a0) % (2 * math.pi)
            if rel <= span:
                return i
        return -1

    def _preview_pet_halo(self, pm):
        key = pm.cacheKey()
        if self._pet_halo_key != key or self._pet_halo_pm is None:
            self._pet_halo_pm = _make_pixel_halo(pm)
            self._pet_halo_key = key
        return self._pet_halo_pm

    def _tick_preview(self):
        """定时器驱动：同步悬停/拖拽标签位置（不能在 paintEvent 里 show/setText）"""
        self._sync_tip_label()
        self.update()

    def _sync_tip_label(self):
        n = len(self.slot_shortcuts)
        target = None
        if self._is_dragging and 0 <= self._drag_source < len(self.slot_shortcuts) and self._drag_pos is not None:
            target = QPointF(self._drag_pos)
            nm = self.slot_shortcuts[self._drag_source].get("name", "")
            if nm and self._tip_label.text() != nm:
                self._tip_label.setText(nm)
            if not self._tip_label.isVisible():
                self._tip_label.show()
        elif self._tip_label.isVisible() and 0 <= self._hover_slot < n:
            target = self._anim_centers[self._hover_slot] if self._hover_slot < len(self._anim_centers) \
                else QPointF(self._slot_rects[self._hover_slot].center())
        if target is not None:
            gp = self.mapToGlobal(QPoint(int(target.x()), int(target.y())))
            self._tip_label.move(gp.x() - self._tip_label.width() // 2,
                                 gp.y() - self._tip_label.height() - 8)

    def _slot_positions(self):
        """当前插槽中心：按键（路径）→位置 + 按序列表"""
        d = {}
        lst = []
        for i in range(min(len(self.slot_shortcuts), len(self._slot_rects))):
            s = self.slot_shortcuts[i]
            d[s.get("path", "")] = self._slot_rects[i].center()
            lst.append(self._slot_rects[i].center())
        return d, lst

    def _start_slot_anim(self, old_pos, old_list):
        """捕获旧位置后启动平滑重排（新按钮从原位/中心出现）"""
        cx, cy = self._layout_cx, self._layout_cy
        self._slot_anim_active = True
        self._slot_anim_t0 = time.monotonic()
        self._slot_anim_from = []
        self._slot_anim_to = []
        self._slot_anim_new = set()
        for i, s in enumerate(self.slot_shortcuts):
            key = s.get("path", "")
            if key in old_pos:
                self._slot_anim_from.append(old_pos[key])
            elif i < len(old_list):
                self._slot_anim_from.append(old_list[i])
                self._slot_anim_new.add(i)
            else:
                self._slot_anim_from.append(QPoint(cx, cy))
                self._slot_anim_new.add(i)
            self._slot_anim_to.append(self._slot_rects[i].center())
        self.update()

    def _apply_change(self, old_pos, old_list):
        """插槽变更后：重算布局 → 平滑重排 → 通知设置窗口"""
        self._layout_dirty = True
        self._ensure_layout()
        self._start_slot_anim(old_pos, old_list)
        self.shortcuts_changed.emit()
        self.update()

    def clear_slots(self):
        """清空插槽（带收拢动画）"""
        old_pos, old_list = self._slot_positions()
        self.slot_shortcuts.clear()
        self._layout_dirty = True
        self._ensure_layout()
        self._start_slot_anim(old_pos, old_list)
        self.update()

    def _pet_circle_radius(self):
        """替换图片的圆形命中区域半径（比图标大 48.5%，即 10% 再放大 35%）"""
        return self._pet_rect.width() / 2.0 * 1.485

    def _pet_circle_contains(self, pos):
        c = self._pet_rect.center()
        dx = pos.x() - c.x()
        dy = pos.y() - c.y()
        r = self._pet_circle_radius()
        return dx * dx + dy * dy <= r * r

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            i = self._find_slot_at(event.pos())
            if 0 <= i < len(self.slot_shortcuts):
                self._drag_source, self._drag_target, self._press_pos = i, -1, event.pos()
                self._is_dragging = False
                self._drag_outside = False

    def mouseMoveEvent(self, event):
        if self._press_pos and not self._is_dragging:
            d = event.pos() - self._press_pos
            if abs(d.x()) > 3 or abs(d.y()) > 3:
                self._is_dragging = True
                self._hover_slot = -1
        if self._is_dragging:
            old = self._drag_target
            self._drag_pos = event.pos()
            self._drag_target = self._find_sector_at(event.pos())
            cx, cy = self.width() // 2, self.height() // 2
            dist = math.hypot(event.pos().x() - cx, event.pos().y() - cy)
            self._drag_outside = (self._disk_outer > 0 and dist > self._disk_outer + 8)
            si = self._find_sector_at(event.pos())
            if si != self._hover_sector:
                self._hover_sector = si
            self.update()
        else:
            old = self._hover_slot
            new_h = self._find_slot_at(event.pos())
            self._hover_slot = new_h
            si = self._find_sector_at(event.pos())
            if si != self._hover_sector:
                self._hover_sector = si
            if self._hover_slot != old:
                self.update()
                if 0 <= self._hover_slot < len(self.slot_shortcuts):
                    name = self.slot_shortcuts[self._hover_slot].get("name", "")
                    if name:
                        self._tip_label.setText(name)
                        gp = event.globalPos()
                        self._tip_label.move(gp.x() - self._tip_label.width() // 2,
                                            gp.y() - self._tip_label.height() - 8)
                        self._tip_label.show()
                    else:
                        self._tip_label.hide()
                else:
                    self._tip_label.hide()

    def mouseReleaseEvent(self, event):
        if self._is_dragging and 0 <= self._drag_source < len(self.slot_shortcuts):
            old_pos, old_list = self._slot_positions()
            if self._drag_outside:
                self.slot_shortcuts.pop(self._drag_source)
                self._apply_change(old_pos, old_list)
            elif 0 <= self._drag_target != self._drag_source >= 0 \
                    and self._drag_target < len(self.slot_shortcuts):
                sc = self.slot_shortcuts
                sc[self._drag_source], sc[self._drag_target] = sc[self._drag_target], sc[self._drag_source]
                self._apply_change(old_pos, old_list)
        self._drag_source = self._drag_target = -1
        self._press_pos = None
        self._is_dragging = False
        self._drag_outside = False
        self._drag_pos = None
        self._tip_label.hide()
        self._hover_sector = -1
        self.update()

    def leaveEvent(self, event):
        self._hover_slot = self._drag_source = self._drag_target = -1
        self._press_pos = None
        self._is_dragging = False
        self._drag_pos = None
        self._hover_sector = -1
        self._drop_over_disk = False
        self._tip_label.hide()
        self.update()

    def contextMenuEvent(self, event):
        if self._pet_circle_contains(event.pos()):
            menu = StyledMenu()
            a_chg = menu.addAction("更换桌宠图片")
            a_rst = menu.addAction("恢复默认图片")
            chosen = menu.exec_(event.globalPos())
            if chosen == a_chg:
                path, _ = QFileDialog.getOpenFileName(self, "选择桌宠图片", "",
                                                       "图片 (*.png *.ico *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*.*)")
                if path:
                    self._pet_image_path = path
                    self.load_pet_image(path)
                    self.pet_image_changed.emit(path)
            elif chosen == a_rst:
                nxt = _next_default_pet_image(self._pet_image_path)
                path = _default_pet_image_abs(nxt)
                self._pet_image_path = nxt
                self.load_pet_image(path if path and os.path.exists(path) else "")
                self.pet_image_changed.emit(nxt)
            return
        i = self._find_slot_at(event.pos())
        if i < 0 or i >= len(self.slot_shortcuts):
            # 空白处右键：多格式添加按钮
            self._empty_context_menu(event.globalPos())
            return
        menu = StyledMenu()
        a_edit = menu.addAction("编辑…（%s）" % _slot_type_label(self.slot_shortcuts[i]))
        menu.addSeparator()
        a_rename = menu.addAction("重命名")
        a_icon = menu.addAction("更换图标")
        a_del = menu.addAction("删除")
        chosen = menu.exec_(event.globalPos())
        if chosen == a_edit:
            self._edit_slot(event.globalPos(), i)
        elif chosen == a_rename:
            name, ok = QInputDialog.getText(self, "重命名", "名称:", text=self.slot_shortcuts[i].get("name", ""))
            if ok and name:
                old_pos, old_list = self._slot_positions()
                self.slot_shortcuts[i]["name"] = name
                self._apply_change(old_pos, old_list)
        elif chosen == a_icon:
            path, _ = QFileDialog.getOpenFileName(self, "选择图标图片", "",
                                                   "图片 (*.png *.ico *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*.*)")
            if path:
                old_pos, old_list = self._slot_positions()
                self.slot_shortcuts[i]["icon"] = path
                self._apply_change(old_pos, old_list)
        elif chosen == a_del:
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.pop(i)
            self._apply_change(old_pos, old_list)

    def _edit_slot(self, pos, idx):
        """按按钮类型打开对应编辑弹窗（设置预览，与悬浮状态一致）。"""
        if idx < 0 or idx >= len(self.slot_shortcuts):
            return

        def set_item(i, new):
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts[i] = new
            self._apply_change(old_pos, old_list)

        _run_edit_slot(pos, idx, lambda i: self.slot_shortcuts[i], set_item, self)

    def _add_url_slot(self, pos):
        """设置预览：弹窗添加网页链接按钮（最多 8 个）。"""
        if len(self.slot_shortcuts) >= 8:
            Toast("插槽已满（最多 8 个）", pos)
            return
        dlg = AddUrlDialog(pos=pos)
        if dlg.exec_() == QDialog.Accepted:
            name, url = dlg.result_pair()
            if not url:
                return
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.append({"name": name, "path": url})
            self._apply_change(old_pos, old_list)

    def _empty_context_menu(self, pos):
        """设置预览：空白处右键菜单，与悬浮状态保持一致。"""
        menu = StyledMenu()
        a_url = menu.addAction("网页链接…")
        a_file = menu.addAction("文件 / 程序…")
        a_dir = menu.addAction("文件夹…")
        a_cmd = menu.addAction("命令…")
        menu.addSeparator()
        a_clear = menu.addAction("清空全部按钮")
        chosen = menu.exec_(pos)
        if chosen == a_url:
            self._add_url_slot(pos)
        elif chosen == a_file:
            self._add_file_slot(pos)
        elif chosen == a_dir:
            self._add_dir_slot(pos)
        elif chosen == a_cmd:
            self._add_cmd_slot(pos)
        elif chosen == a_clear:
            self._clear_all_slots(pos)

    def _add_file_slot(self, pos):
        if len(self.slot_shortcuts) >= 8:
            Toast("插槽已满（最多 8 个）", pos)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件或程序", "",
            "程序 / 快捷方式 (*.exe *.lnk *.url *.bat *.cmd *.ps1);;所有文件 (*.*)")
        if path:
            name = os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.append({"name": name, "path": path})
            self._apply_change(old_pos, old_list)

    def _add_dir_slot(self, pos):
        if len(self.slot_shortcuts) >= 8:
            Toast("插槽已满（最多 8 个）", pos)
            return
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if path:
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.append({"name": os.path.basename(path) or path,
                                        "path": path})
            self._apply_change(old_pos, old_list)

    def _add_cmd_slot(self, pos):
        if len(self.slot_shortcuts) >= 8:
            Toast("插槽已满（最多 8 个）", pos)
            return
        dlg = AddCommandDialog(pos=pos)
        if dlg.exec_() == QDialog.Accepted:
            name, cmd = dlg.result_pair()
            if not cmd:
                return
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.append({"name": name, "path": "run://" + cmd})
            self._apply_change(old_pos, old_list)

    def _clear_all_slots(self, pos):
        if not self.slot_shortcuts:
            Toast("当前没有按钮", pos)
            return
        cp = ConfirmPopup("确定清空全部按钮？此操作不可撤销")
        if cp.exec_(pos):
            old_pos, old_list = self._slot_positions()
            self.slot_shortcuts.clear()
            self._apply_change(old_pos, old_list)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._ext_drag_active = True
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        old = self._hover_slot
        self._hover_slot = self._find_slot_at(event.pos())
        self._ext_hover_pet = self._pet_circle_contains(event.pos())
        cx, cy, _, _ = self._ensure_layout()
        dx = event.pos().x() - cx
        dy = event.pos().y() - cy
        self._drop_over_disk = (self._disk_outer > 0 and math.hypot(dx, dy) <= self._disk_outer + 8)
        full = len(self.slot_shortcuts) >= 8
        si = self._find_sector_at(event.pos()) if full else -1
        if si != self._hover_sector:
            self._hover_sector = si
        if self._hover_slot != old or self._drop_over_disk:
            self.update()
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._ext_drag_active = False
        self._ext_hover_pet = False
        self._hover_slot = -1
        self._hover_sector = -1
        self._drop_over_disk = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._ext_drag_active = False
        self._hover_sector = -1
        self._drop_over_disk = False
        if event.mimeData().hasUrls():
            old_pos, old_list = self._slot_positions()
            if len(self.slot_shortcuts) >= 8:
                target = self._find_sector_at(event.pos())
            else:
                target = self._find_slot_at(event.pos())
            # 拖到桌宠图标圆形区域：图片/动图直接替换桌宠图片
            if self._pet_circle_contains(event.pos()):
                for url in event.mimeData().urls():
                    path = url.toLocalFile()
                    if path and os.path.isfile(path) and os.path.splitext(path)[1].lower() in \
                            (".png", ".ico", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                        self._pet_image_path = path
                        self.load_pet_image(path)
                        self.pet_image_changed.emit(path)
                        self._ext_hover_pet = False
                        self.update()
                        event.acceptProposedAction()
                        return
            empty = target < 0 and 0 <= event.pos().x() < self.width() and 0 <= event.pos().y() < self.height()
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                # 本地任意文件/文件夹或网盘路径都接受
                if path and (os.path.isfile(path) or os.path.isdir(path)):
                    name = os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)
                    sc = {"name": name, "path": path}
                    if 0 <= target < len(self.slot_shortcuts):
                        self.slot_shortcuts[target] = sc
                    elif empty and len(self.slot_shortcuts) < 8:
                        self.slot_shortcuts.append(sc)
                    self._apply_change(old_pos, old_list)
                    break
                # 浏览器拖来的网址链接
                u = url.url()
                if u.startswith(("http://", "https://")):
                    sc = {"name": u.split("//")[-1].split("/")[0], "path": u}
                    if 0 <= target < len(self.slot_shortcuts):
                        self.slot_shortcuts[target] = sc
                    elif empty and len(self.slot_shortcuts) < 8:
                        self.slot_shortcuts.append(sc)
                    self._apply_change(old_pos, old_list)
                    break
            event.acceptProposedAction()
        else:
            event.ignore()
        self._hover_slot = self._ext_hover = -1
        self._ext_hover_pet = False
        self.update()


# ==================== 自定义规则编辑对话框 ====================

class _RuleRow(QWidget):
    """规则列表行：名称/类型/间隔 + 右侧启用勾选框（点击行可选中，右键弹菜单）"""

    _MENU_QSS = (
        "QMenu{background:#232a3a;color:#e8ecf5;border:1px solid "
        "rgba(255,255,255,40);padding:2px;}"
        "QMenu::item{padding:4px 16px;font-size:10px;}"
        "QMenu::item:selected{background:rgba(74,144,226,130);border-radius:3px;}"
        "QMenu::item:disabled{color:rgba(200,215,240,70);}"
    )

    def __init__(self, listw, idx, rule, dlg=None):
        super().__init__()
        self._list = listw
        self._idx = idx
        self._dlg = dlg
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        self.grip = _GripLabel(self)
        self.grip.setCursor(Qt.OpenHandCursor)
        self.grip.setFixedWidth(14)
        self.grip.setAlignment(Qt.AlignCenter)
        self.grip.setStyleSheet("color:#8a92a6; font-size:11px;")
        self.grip.setToolTip("拖动排序")
        lay.addWidget(self.grip)
        st = (rule.get("source") or {}).get("type", "static")
        marker = " ⚡" if rule.get("popup") else ""
        # 只有"自动弹出"模块才有执行间隔；嵌入气泡模块不显示秒数
        suffix = "  %ss" % rule.get("interval", 60) if rule.get("popup") else ""
        lab = QLabel("%s%s  [%s]%s" % (rule.get("name", "规则"), marker, st, suffix))
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(lab)
        lay.addStretch()
        self.cb = QCheckBox()
        self.cb.setChecked(bool(rule.get("enabled", True)))
        self.cb.setToolTip("勾选 = 启用该规则")
        lay.addWidget(self.cb)

    def mousePressEvent(self, e):
        self._list.setCurrentRow(self._idx)
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        """右键：导出 / 删除（操作当前行）"""
        self._list.setCurrentRow(self._idx)
        if self._dlg is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(self._MENU_QSS)
        act_export = menu.addAction("导出")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec_(e.globalPos())
        if chosen == act_export:
            self._dlg._export_one_rule(self._idx)
        elif chosen == act_del:
            self._dlg._del_status_rule()


class _GripLabel(QLabel):
    """拖拽排序手柄：按住拖动行到目标位置"""

    def __init__(self, row):
        super().__init__("≡")
        self._row = row
        self._pos = None

    def mousePressEvent(self, e):
        self._row._list.setCurrentRow(self._row._idx)
        self._pos = e.pos()
        e.accept()

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.LeftButton and self._pos is not None
                and (e.pos() - self._pos).manhattanLength() > 8):
            self._pos = None
            self._row._list.startDrag(Qt.MoveAction)
        e.accept()


class _RuleListWidget(QListWidget):
    """支持拖拽排序的规则列表：拖动手柄把行移动到目标位置"""

    def __init__(self, on_reorder=None, parent=None):
        super().__init__(parent)
        self._on_reorder = on_reorder
        self._drag_row = -1
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SingleSelection)

    def startDrag(self, supportedActions):
        self._drag_row = self.currentRow()
        try:
            # 用透明空图作为拖拽图，避免鼠标旁出现选中高亮色块
            drag = QDrag(self.viewport())
            drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
            pm = QPixmap(1, 1)
            pm.fill(Qt.transparent)
            drag.setPixmap(pm)
            drag.exec_(supportedActions, Qt.MoveAction)
        except Exception:
            pass

    def dropEvent(self, event):
        src = self._drag_row if self._drag_row >= 0 else self.currentRow()
        dst = self.indexAt(event.pos()).row()
        event.acceptProposedAction()
        self._drag_row = -1
        if dst < 0:
            dst = self.count() - 1   # 拖到最下方：追加到末尾，而不是删除
        if src >= 0 and dst >= 0 and src != dst and self._on_reorder is not None:
            QTimer.singleShot(0, lambda: self._on_reorder(src, dst))


class _JsonPlaceholderHighlighter(QSyntaxHighlighter):
    """占位符高亮已停用：模板一律不标红（避免误导用户以为要改）。
    保留类结构以兼容调用点，实际不再渲染红色。"""

    def __init__(self, doc):
        super().__init__(doc)
        self._fmt = QTextCharFormat()
        self._fmt.setForeground(QColor(208, 80, 80))
        self._fmt.setFontWeight(QFont.Bold)
        self.script_mode = False

    def highlightBlock(self, text):
        # 不再标红：模板里的 <...> 只是占位示例，不强制用户改动
        pass


class _RuleTestBridge(QObject):
    """规则测试的后台线程 -> UI 信号桥（避免联网测试卡死界面）。"""

    done = pyqtSignal(str, str, object)   # (结果文本, 错误信息, RuleProvider)


_RULE_DIALOG_REF = [None]   # 弱引用：同一时间只允许一个模块编辑窗口


class RuleDialog(QDialog):
    """自定义状态规则编辑：预设下拉 + 类型模板 + JSON 配置 + 即时测试"""

    _presets = [
        # (类型, 标签, 名称, 配置)
        ("script", "番茄时钟", "番茄时钟", {
            "source": {"type": "script", "lang": "python", "ui": "pomodoro",
                       "code": (
                           "# 番茄时钟脚本：修改下方秒数即可调整工作时长/休息时长\n"
                           "st = state\n"
                           "st.setdefault(\"work\", 25 * 60)\n"
                           "st.setdefault(\"break\", 5 * 60)\n"
                           "result = \"番茄时钟\"\n")},
            "interval": 5, "fallback": "未启动"}),
        ("script", "聚合AI", "聚合AI", {
            "source": {"type": "script", "lang": "python", "ui": "webchat",
                       "url": "https://chat.deepseek.com",
                       "code": "result = '聚合AI'", "timeout": 5},
            "interval": 3600, "fallback": "组件未加载",
            "chat": True}),
        ("script", "无限画布", "无限画布", {
            "source": {"type": "script", "lang": "python", "ui": "canvas",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "便签", "便签", {
            "source": {"type": "script", "lang": "python", "ui": "notes",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "倒计时器", "倒计时器", {
            "source": {"type": "script", "lang": "python", "ui": "countdown",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "计算器", "计算器", {
            "source": {"type": "script", "lang": "python", "ui": "calc",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "快捷启动", "快捷启动", {
            "source": {"type": "script", "lang": "python", "ui": "launcher",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "健康打卡", "健康打卡", {
            "source": {"type": "script", "lang": "python", "ui": "health",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "Token消耗", "Token消耗", {
            "source": {"type": "script", "lang": "python", "ui": "tokenmeter",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "统计表", "统计表", {
            "source": {"type": "script", "lang": "python", "ui": "stats",
                       "code": "result = ''",
                       "stats": {"metric": "todos", "unit": " 项"}},
            "interval": 60, "fallback": ""}),
        ("script", "拼豆", "拼豆", {
            "source": {"type": "script", "lang": "python", "ui": "perler",
                       "code": "result = ''"},
            "interval": 3600, "fallback": ""}),
        ("script", "磁盘剩余空间", "磁盘剩余空间", {
            "source": {"type": "script", "lang": "python",
                       "code": ("import shutil\n"
                                "total, used, free = shutil.disk_usage(\"C:/\")\n"
                                "result = \"C盘剩余 %.0f GB / 共 %.0f GB\" % "
                                "(free / 2**30, total / 2**30)")},
            "interval": 3600, "fallback": "脚本错误"}),
        ("script", "程序运行检测", "程序运行检测", {
            "source": {"type": "script", "lang": "python",
                       "code": ("import subprocess\n"
                                "r = subprocess.run([\"tasklist\"], "
                                "capture_output=True, text=True)\n"
                                "result = \"VS Code 运行中\" if \"Code.exe\" in "
                                "r.stdout else \"VS Code 未运行\"")},
            "interval": 30, "fallback": "脚本错误"}),
        ("script", "网络检测", "网络检测", {
            "source": {"type": "script", "lang": "python",
                       "code": ("import subprocess\n"
                                "try:\n"
                                "    r = subprocess.run([\"ping\", \"-n\", \"1\", "
                                "\"baidu.com\"], capture_output=True, text=True, timeout=8)\n"
                                "    result = \"网络正常\" if r.returncode == 0 else \"网络不通\"\n"
                                "except Exception:\n"
                                "    result = \"网络不通\"")},
            "interval": 60, "fallback": "脚本错误"}),
        ("script", "随机鼓励语", "随机鼓励语", {
            "source": {"type": "script", "lang": "python",
                       "code": ("import random\n"
                                "result = random.choice([\"代码写得好，bug 自然少\", "
                                "\"稳住，能赢！\", \"累了就喝口水 ☕\", \"别急，答案正在路上\"])")},
            "interval": 3600, "fallback": "脚本错误"}),
        ("script", "节日倒计时", "节日倒计时", {
            "source": {"type": "script", "lang": "python",
                       "code": ("from datetime import datetime\n"
                                "result = \"距国庆还有 %d 天\" % "
                                "(datetime(2026, 10, 1) - datetime.now()).days")},
            "interval": 3600, "fallback": "脚本错误"}),
        ("llm", "AI助手", "AI助手", {
            "source": {"type": "llm", "base_url": "https://api.deepseek.com/v1",
                       "model": "deepseek-chat", "api_key": "<你的API Key>",
                       "system_prompt":
                           "你是桌宠的 AI 助手。你拥有这些能力（看到相关需求必须调用"
                           "对应工具来真正执行，禁止只口头说'已设置/已删除'而不调用"
                           "工具）：查时间/开网页/设置定时提醒；管理待办（添加、查看、"
                           "删除）；管理气泡模块（列出、新建、删除、启停、排序）；管理"
                           "径向菜单按钮（列出、添加、删除、排序、编辑）；控制桌宠本体"
                           "（隐藏/显示/移动）；调整桌宠设置（大小/透明度等，先 pet_info"
                           "查看当前值再 pet_setting 修改）；画画（用户说'画布/无限画布/绘图'"
                            "用 canvas_draw，说'拼豆/像素画/像素点'用 perler_draw，"
                            "两者都用中文描述要画的内容）。用户需要提取/整理为固定"
                           "格式（如 JSON、列表）时，直接输出对应结构，不要附加解释。"
                           "其余问题直接简短回答。",
                       "user_prompt": "你好", "temperature": 0.8, "max_tokens": 8192,
                       "tools": ["get_time", "add_todo", "list_todos", "open_url",
                                 "remind", "delete_todo", "list_modules",
                                 "add_module", "remove_module", "enable_module",
                                 "move_module", "list_buttons", "add_button",
                                 "remove_button", "move_button", "edit_button",
                                 "pet_control", "pet_setting", "pet_info",
                                 "list_components", "list_templates",
                                 "add_module_from_template", "canvas_draw", "perler_draw"]},
            "chat": True,
            "greeting": "你好！我是你的 AI 助手，可以：管理待办/模块/按钮、"
                        "控制桌宠、调整桌宠设置、查时间、开网页、设提醒。"
                        "试试说「把桌宠调大一点」或「删掉径向菜单里的 B 站按钮」。",
            "fallback": "AI 不可用"}),
        ("llm", "AI本地（Ollama）", "AI本地·Ollama", {
            "source": {"type": "llm", "base_url": "http://localhost:11434/v1",
                       "model": "qwen2.5:7b", "api_key": "ollama",
                       "system_prompt": "你是桌宠助手，用简短中文回复", "user_prompt": "你好",
                       "temperature": 0.8, "max_tokens": 8192},
            "chat": True,
            "greeting": "你好！我是本地 Ollama 模型（免费离线）。"
                        "需要先运行 ollama serve 并 ollama pull qwen2.5:7b。",
            "fallback": "Ollama 不可用"}),
        ("llm", "AI聊天·OpenAI兼容", "AI聊天·OpenAI兼容", {
            "source": {"type": "llm", "base_url": "<https://api.xxx.com/v1，OpenAI兼容>",
                       "model": "<模型名>", "api_key": "<你的API Key>",
                       "system_prompt": "你是桌宠助手，用简短中文回复", "user_prompt": "你好",
                       "temperature": 0.8, "max_tokens": 8192},
            "chat": True, "greeting": "你好呀！我是你的桌宠助手，想聊什么？",
            "fallback": "AI 不可用"}),
        ("http", "天气", "天气", {
            "source": {"type": "http", "url": "https://wttr.in/?format=%c+%t",
                       "timeout": 5, "plain_text": True},
            "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "$1"},
            "fallback": "天气获取失败"}),
        ("http", "公网IP", "公网IP", {
            "source": {"type": "http", "url": "https://api.ip.sb/ip", "timeout": 5},
            "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "IP：$1"},
            "fallback": "IP 获取失败"}),
        ("http", "上证指数", "上证指数", {
            "source": {"type": "http", "url": "https://hq.sinajs.cn/list=sh000001",
                       "headers": {"Referer": "https://finance.sina.com.cn"}, "timeout": 5},
            "transform": {"type": "text", "pattern": "^[^\"]*\"[^,]*,([0-9.]+).*$",
                           "replacement": "上证 $1"},
            "fallback": "行情获取失败"}),
        ("http", "头条新闻", "头条新闻", {
            "source": {"type": "http", "url": "https://60s.viki.moe/v2/60s", "timeout": 5},
            "transform": {"type": "jsonpath", "path": "data.news"},
            "max_chars": 80, "fallback": "新闻获取失败"}),
        ("http", "美元汇率", "美元汇率", {
            "source": {"type": "http", "url": "https://api.frankfurter.app/latest?from=USD&to=CNY", "timeout": 5},
            "transform": {"type": "template", "template": "1 USD = {value} CNY"},
            "fallback": "汇率获取失败"}),
        ("clock", "当前时间", "当前时间", {
            "source": {"type": "clock", "mode": "time"}}),
        ("clock", "下班倒计时", "下班倒计时", {
            "source": {"type": "clock", "mode": "countdown", "target": "18:00"}}),
        ("llm", "AI趣味", "AI趣味", {
            "source": {"type": "llm", "base_url": "<https://api.deepseek.com/v1>",
                       "model": "<deepseek-chat>", "api_key": "<你的API Key>",
                       "system_prompt":
                           "你是一只桌面宠物，每次用一句话说一件有趣的事"
                           "（问候/冷知识/有意义的话均可）",
                       "user_prompt": "开始", "temperature": 0.9, "max_tokens": 80},
            "fallback": "AI 不可用"}),
        ("static", "固定文本", "固定文本", {
            "source": {"type": "static", "text": "摸鱼中"},
            "interval": 3600}),
    ]

    _templates = {
        "http": ('{\n'
                  '  "source": {"type": "http", "url": "https://", "timeout": 5},\n'
                  '  "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "$1"},\n'
                  '  "fallback": "获取失败"\n'
                  '}'),
        "llm": ('{\n'
                 '  "source": {"type": "llm", "base_url": "<https://api.xxx.com/v1，OpenAI兼容>",\n'
                 '            "model": "<模型名>", "api_key": "<你的API Key>",\n'
                 '            "system_prompt": "你是一只桌面宠物", "user_prompt": "用一句话回复",\n'
                 '            "temperature": 0.8, "max_tokens": 8192},\n'
                 '  "fallback": "AI 不可用"\n'
                 '}'),
        "clock": '{\n  "source": {"type": "clock", "mode": "time"}\n}',
        "script": ('{\n'
                   '  "source": {"type": "script", "lang": "python", "ui": "",\n'
                   '            "code": "result = \\"你好\\""},\n'
                   '  "fallback": "脚本错误"\n'
                   '}'),
        "script_javascript": ('{\n'
                              '  "source": {"type": "script", "lang": "javascript", "ui": "",\n'
                              '            "code": "console.log(\\"你好\\")"},\n'
                              '  "fallback": "脚本错误"\n'
                              '}'),
        "script_shell": ('{\n'
                         '  "source": {"type": "script", "lang": "shell", "ui": "",\n'
                         '            "code": "echo 你好"},\n'
                         '  "fallback": "脚本错误"\n'
                         '}'),
        "agent": ('{\n'
                  '  "source": {"type": "agent", "endpoint": "<http://localhost:PORT/requests>",\n'
                  '            "approve": "<http://localhost:PORT/approve>", "api_key_env": "",\n'
                  '            "timeout": 5},\n'
                  '  "fallback": "连接失败"\n'
                  '}'),
        "static": '{\n  "source": {"type": "static", "text": "摸鱼中"}\n}',
    }

    # 脚本类预设的多语言常用写法（语言勾选切换时自动转译）
    _LANG_CODE = {
        "pomodoro_python": ("# 番茄时钟：修改下方秒数即可调整工作时长/休息时长\n"
                            "st = state\n"
                            "st.setdefault(\"work\", 25 * 60)\n"
                            "st.setdefault(\"break\", 5 * 60)\n"
                            "result = \"番茄时钟\"\n"),
        "pomodoro_javascript": ("// 番茄时钟（JavaScript/Node 常用写法）\n"
                                "const work = 25 * 60, brk = 5 * 60;\n"
                                "console.log('工作中 ' + String(Math.floor(work / 60)).padStart(2, '0')\n"
                                "    + ':00 · 休息 ' + String(Math.floor(brk / 60)).padStart(2, '0') + ':00');\n"),
        "pomodoro_shell": ("# 番茄时钟（Shell 常用写法）\n"
                           "echo \"工作中 25:00 · 休息 05:00\"\n"),
    }

    def __init__(self, parent=None, rule=None):
        super().__init__(parent)
        self.setWindowTitle("自定义模块")
        self.setWindowIcon(_asset_icon("settings_icon.png"))
        self.setMinimumWidth(420)
        self.setFont(QFont("SimHei"))
        # 标题栏"？"按钮：点击打开开发文档（见 event 拦截）
        self.setWindowFlags(self.windowFlags() | Qt.WindowContextHelpButtonHint)
        _qss = ("QDialog{background:#eef2f8;} QLabel{color:#3a3f55;font-size:11px;}"
                "QLineEdit,QComboBox,QSpinBox,QPlainTextEdit,QCheckBox{color:#3a3f55;font-size:11px;}"
                "QPushButton{color:#3a3f55;font-size:11px;} QGroupBox{color:#5a6178;}")
        for _o, _n in SettingsDialog._DARK_SUBS:
            _qss = _qss.replace(_o, _n)
        self.setStyleSheet(_qss)
        QTimer.singleShot(0, lambda: _set_dark_titlebar(self))
        self.rule = None
        self._rule_id = (rule or {}).get("id", "r%d" % int(time.time() * 1000))
        self._orig_interval = int((rule or {}).get("interval", 5) or 5)
        self._last_type = ""
        self._editing = rule is not None
        self._builtin = bool((rule or {}).get("builtin"))
        self._orig_builtin = (rule or {}).get("builtin")   # 内置标记原值（编辑后仍保留为内置）
        self._orig_enabled = bool((rule or {}).get("enabled", True))
        self._lang = "python"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        row0 = QHBoxLayout()
        row0.addWidget(QLabel("预设"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("自定义", None)
        for i, (label, ptype, name, body) in enumerate(self._presets):
            self.preset_combo.addItem(label, i)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        row0.addWidget(self.preset_combo, 1)
        row0.addWidget(QLabel("类型"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["script", "llm", "http", "clock", "static"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        row0.addWidget(self.type_combo)
        lay.addLayout(row0)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：天气、服务器状态、番茄时钟脚本")
        row1.addWidget(self.name_edit, 1)
        self.chat_cb = QCheckBox("对话模式")
        self.chat_cb.setToolTip("启用后气泡下方出现对话面板，可自由对话（大模型类型）")
        self.chat_cb.toggled.connect(self._on_chat_toggle)
        row1.addWidget(self.chat_cb)
        lay.addLayout(row1)

        cfg_row = QHBoxLayout()
        cfg_row.addWidget(QLabel("模块配置"))
        cfg_row.addStretch()
        self.lang_note = QLabel("脚本类支持：Python / JavaScript / Shell")
        self.lang_note.setToolTip(
            "语言字段只对脚本（script）类模块有意义：桌宠按 lang 选择解释器运行。\n"
            "大模型（llm）类模块是纯 API 调用，识别的是自然语言提示词，与脚本语言无关。")
        self.lang_note.setStyleSheet("color:#9aa2b2;font-size:10px;")
        cfg_row.addWidget(self.lang_note)
        cfg_row.addStretch()
        self.widgets_btn = QPushButton("组件文件夹")
        self.widgets_btn.setStyleSheet("min-width:0; padding:2px 8px; font-size:11px;")
        self.widgets_btn.setToolTip("打开 widgets/ 组件插件目录（新建组件后需重启桌宠生效）")
        self.widgets_btn.setAutoDefault(False)   # 回车不应触发它（避免误开组件目录）
        self.widgets_btn.setDefault(False)
        self.widgets_btn.clicked.connect(self._open_widgets_folder)
        cfg_row.addWidget(self.widgets_btn)
        lay.addLayout(cfg_row)
        self.cfg_edit = QPlainTextEdit()
        self.cfg_edit.setMinimumHeight(160)  # 配置区弹性高度：弹出选项展开时自动让位
        self.cfg_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.cfg_edit, 1)
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color:#d05050; font-size:10px;")
        self.hint_label.setFixedHeight(18)   # 固定高度，避免被布局拉伸成大空档
        self.hint_label.hide()               # 无提示时不留空档
        lay.addWidget(self.hint_label)
        self._hl = _JsonPlaceholderHighlighter(self.cfg_edit.document())
        # 文本变化只刷新提示文字；rehighlight 会再触发 textChanged，不能在信号里调用
        self.cfg_edit.textChanged.connect(lambda: self._update_hint(False))
        if self._builtin:
            self.hint_label.setText("内置模块：与普通模块一样可编辑保存；在设置中可删除")
            self.hint_label.show()

        self.embed_cb = QCheckBox("嵌入气泡")
        self.embed_cb.setToolTip("勾选后在气泡中显示该模块（与自动弹出二选一）")
        self.embed_cb.setStyleSheet("font-size:11px;")
        self.embed_cb.setChecked(True)   # 新模块默认嵌入气泡
        self.embed_cb.toggled.connect(self._on_embed_toggle)
        self.popup_cb = QCheckBox("自动弹出")
        self.popup_cb.setToolTip("内容变化时以独立小气泡浮出显示（类似情绪气泡），与嵌入气泡二选一")
        self.popup_cb.setStyleSheet("font-size:11px;")
        self.popup_cb.toggled.connect(self._on_popup_toggle)
        self.popup_interval = QSpinBox()
        self.popup_interval.setRange(5, 86400)
        self.popup_interval.setSuffix(" 秒")
        self.popup_interval.setFixedWidth(70)
        self.popup_interval.setToolTip("自动弹出的执行/检查间隔")
        self.popup_duration = QSpinBox()
        self.popup_duration.setRange(1, 600)
        self.popup_duration.setSuffix(" 秒")
        self.popup_duration.setFixedWidth(70)
        self.popup_duration.setToolTip("自动弹出时气泡显示的持续时间")
        self.popup_interval.hide()   # 未勾选自动弹出时收起间隔/时长选项
        self.popup_duration.hide()

        # 底部：左侧测试区（与模块列表下方测试框完全同尺寸，占 150 高）
        # 右侧竖排：嵌入气泡 / 自动弹出 / [间隔·时长] / 测试 / 取消·确定，
        # 在测试框与窗口右边界之间居中，与测试框等高
        bottom = QHBoxLayout()
        bottom.setSpacing(0)   # 间距由两侧 stretch 提供，保证按钮列真正居中
        bottom.setContentsMargins(0, 0, 0, 0)   # 清掉默认边距，让底部贴齐窗口下沿
        self.result_view = ResultView(self, pin_size=QSize(270, 150))
        bottom.addWidget(self.result_view)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(5)
        right_col.addWidget(self.embed_cb)
        right_col.addWidget(self.popup_cb)
        right_col.addWidget(self.popup_interval)
        right_col.addWidget(self.popup_duration)
        right_col.addStretch()
        tbtn = QPushButton("测试")
        tbtn.setFixedWidth(84)   # 与取消/确定等宽，容纳 QSS 内边距，避免文字被裁
        tbtn.clicked.connect(self._test)
        right_col.addWidget(tbtn, 0, Qt.AlignLeft)
        # 取消 / 确定：与"测试"按钮左对齐，纵向堆叠，窗口右缘贴近按钮
        self.ok_btn = None
        for t, fn in [("取消", self.reject), ("确定", self._accept)]:
            b = QPushButton(t)
            b.setFixedWidth(84)
            b.clicked.connect(fn)
            if t == "确定":
                b.setObjectName("primary")
                b.setDefault(True)   # 回车触发"确定"，而不是打开组件文件夹
                self.ok_btn = b
            right_col.addWidget(b, 0, Qt.AlignLeft)
        right_wrap = QWidget()
        right_wrap.setMinimumHeight(150)   # 随内容自适应（自动弹出选项展开时增高）
        right_wrap.setFixedWidth(88)     # 容纳按钮（84）+ 数值框（70）
        right_wrap.setLayout(right_col)
        bottom.addStretch(1)             # 让按钮列在测试框与右边界之间居中
        bottom.addWidget(right_wrap)
        bottom.addStretch(1)
        lay.addLayout(bottom)

        if rule:
            self._load_rule(rule)
        else:
            self.type_combo.setCurrentText("static")
        self._refresh_presets()
        # 整个窗口固定大小，不允许自由调整；高度容纳"自动弹出"选项展开的情况，
        # 多余空间由配置区弹性吸收，切换选项时窗口不跳动
        self.setFixedSize(430, 545)

    def _template_for_script(self):
        return self._templates.get("script_" + self._lang, self._templates.get("script", ""))

    def _widgets_dir(self):
        """自定义组件插件目录（widgets/）。"""
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "widgets")

    def _open_widgets_folder(self):
        """在资源管理器中打开 widgets/ 组件插件目录。"""
        try:
            d = self._widgets_dir()
            if os.path.isdir(d):
                import subprocess
                subprocess.Popen(["explorer", d])
            else:
                self.hint_label.setText("未找到组件目录：%s" % d)
                self.hint_label.show()
        except Exception as e:
            self.hint_label.setText("打开组件目录失败：%s" % e)
            self.hint_label.show()

    def event(self, e):
        """标题栏"？"按钮：拦截 What's This 模式，改为打开开发文档。"""
        if e.type() == QEvent.EnterWhatsThisMode:
            self._open_dev_doc()
            return True
        return super().event(e)

    def _open_dev_doc(self):
        """打开自定义模块开发文档（自定义模块开发指南.md）。"""
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "自定义模块开发指南.md")
            if os.path.exists(p):
                os.startfile(p)
            else:
                self.hint_label.setText("未找到开发文档：%s" % p)
                self.hint_label.show()
        except Exception as e:
            self.hint_label.setText("打开开发文档失败：%s" % e)
            self.hint_label.show()

    def _load_rule(self, rule):
        self.name_edit.setText(str(rule.get("name", "")))
        popup = bool(rule.get("popup", False))
        embed = bool(rule.get("embed", True))
        if popup and embed:
            # 嵌入/自动弹出互斥：情绪保留弹出，其余（含对话类）保留嵌入
            if rule.get("builtin") == "mood":
                embed = False
            else:
                popup = False
        self.popup_cb.blockSignals(True)
        self.popup_cb.setChecked(popup)
        self.popup_cb.blockSignals(False)
        self.popup_interval.setValue(int(rule.get("interval", 60) or 60))
        self.popup_interval.setVisible(popup)
        self.popup_duration.setValue(int(rule.get("popup_duration", 3) or 3))
        self.popup_duration.setVisible(popup)
        self.embed_cb.blockSignals(True)
        self.embed_cb.setChecked(embed)
        self.embed_cb.blockSignals(False)
        self.chat_cb.setChecked(bool(rule.get("chat", False)))
        self._on_chat_toggle(self.chat_cb.isChecked())
        st = rule.get("source") or {}
        self.type_combo.blockSignals(True)
        self.type_combo.setCurrentText(st.get("type", "static"))
        self.type_combo.blockSignals(False)
        self.preset_combo.setCurrentIndex(0)
        body = {k: v for k, v in rule.items() if k in ("source", "transform", "fallback")}
        self.cfg_edit.setPlainText(json.dumps(body, ensure_ascii=False, indent=2))
        self._last_type = st.get("type", "static")
        self._lang = st.get("lang", "python") or "python"
        self._update_hint(True)

    def _on_embed_toggle(self, on):
        # 嵌入/自动弹出互斥：只能选一个
        if on and self.popup_cb.isChecked():
            self.popup_cb.setChecked(False)

    def _on_popup_toggle(self, on):
        # 嵌入/自动弹出互斥：只能选一个
        if on and self.embed_cb.isChecked():
            self.embed_cb.setChecked(False)
        self.popup_interval.setVisible(on)
        self.popup_duration.setVisible(on)

    def _on_chat_toggle(self, on):
        """对话模式下无需额外设置（间隔/字数由代码实现）"""
        pass

    def _refresh_presets(self):
        """按当前类型过滤预设列表"""
        t = self.type_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("自定义", None)
        for i, (ptype, label, name, body) in enumerate(self._presets):
            if ptype == t:
                self.preset_combo.addItem(label, i)
        self.preset_combo.blockSignals(False)
        self.preset_combo.setCurrentIndex(0)

    def _on_preset(self, idx):
        pi = self.preset_combo.itemData(idx)
        if pi is None:
            # 自定义：回到当前类型的通用模板
            t = self.type_combo.currentText()
            self.cfg_edit.setPlainText(self._template_for_script() if t == "script"
                                       else self._templates.get(t, ""))
            return
        ptype, label, name, body = self._presets[pi]
        self.type_combo.blockSignals(True)
        self.type_combo.setCurrentText(ptype)
        self.type_combo.blockSignals(False)
        self.name_edit.setText(name)
        self.cfg_edit.setPlainText(json.dumps(body, ensure_ascii=False, indent=2))
        self._lang = (body.get("source") or {}).get("lang", "python") or "python"
        self.chat_cb.setChecked(bool(body.get("chat", False)))
        self._on_chat_toggle(self.chat_cb.isChecked())
        self._last_type = ptype
        self._refresh_presets()
        self._update_hint(True)

    def _update_hint(self, rehighlight=False):
        if self._builtin:
            self.hint_label.setText("内置模块：与普通模块一样可编辑保存；在设置中可删除")
            self.hint_label.show()
            return
        is_script = (self.type_combo.currentText() == "script")
        # 高亮已停用：仅保持 script_mode 状态同步（无实际标红）
        if self._hl.script_mode != is_script:
            self._hl.script_mode = is_script
            self._hl.rehighlight()
        elif rehighlight:
            self._hl.rehighlight()
        if is_script:
            self.hint_label.setText("")
            self.hint_label.hide()
            return
        has_ph = "<" in self.cfg_edit.toPlainText()
        if has_ph:
            self.hint_label.setText("<> 占位内容：请改成你的真实配置（如 API Key）")
            self.hint_label.show()
        else:
            self.hint_label.setText("")
            self.hint_label.hide()

    def _on_type_changed(self, t):
        # 切类型时总是刷新为该类型模板（编辑已有规则时保留原内容）
        if not getattr(self, "_editing", False):
            self.cfg_edit.setPlainText(self._template_for_script() if t == "script"
                                       else self._templates.get(t, ""))
        self._last_type = t
        if t == "llm" and not getattr(self, '_editing', False):
            self.chat_cb.setChecked(True)   # 大模型默认打开对话模式
        elif t != "llm":
            self.chat_cb.setChecked(False)
        self.chat_cb.setEnabled(t == "llm")
        self._on_chat_toggle(self.chat_cb.isChecked())
        self._refresh_presets()
        self._update_hint(True)

    def _build_rule(self):
        body = {}
        raw = self.cfg_edit.toPlainText().strip()
        if raw:
            body = json.loads(raw)
        rule = {
            "id": self._rule_id,
            "name": self.name_edit.text().strip() or "规则",
            "interval": self.popup_interval.value() if self.popup_cb.isChecked()
                        else self._orig_interval,
            "enabled": self._orig_enabled and (self.embed_cb.isChecked()
                                               or self.popup_cb.isChecked()),
            "embed": self.embed_cb.isChecked(),
            "popup": self.popup_cb.isChecked(),
            "popup_duration": self.popup_duration.value() if self.popup_cb.isChecked() else 3,
            "chat": self.chat_cb.isChecked() and self.type_combo.currentText() == "llm",
        }
        # 编辑内置模块：保留内置标记，改完仍是内置（不迁移到自定义列表）
        if self._orig_builtin:
            rule["builtin"] = self._orig_builtin
        rule.update(body)
        if not isinstance(rule.get("source"), dict):
            rule["source"] = {"type": self.type_combo.currentText()}
        src = rule.get("source")
        if isinstance(src, dict) and src.get("type") == "script":
            src["lang"] = self._lang
        return rule

    def _test(self):
        try:
            r = self._build_rule()
        except Exception as e:
            self.result_view.show_result("", "配置错误：%s" % e)
            return
        self.result_view.show_result("", "测试中…")
        bridge = _RuleTestBridge(self)
        bridge.done.connect(self._on_test_done)

        def work():
            val, err, rp = "", "", None
            try:
                from status_monitor import RuleProvider
                rp = RuleProvider(r)
                val = rp.collect()
                err = rp._last_error or ""
            except Exception as e:
                err = str(e)
            try:
                bridge.done.emit(val, err, rp)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, val, err, rp):
        ui = rp._source().get("ui") if rp is not None else ""
        if ui:
            if ui == "pomodoro":
                from h5_cards import PomodoroCard
                card = PomodoroCard(state=rp.state)
                card.setFixedWidth(184)   # 与气泡卡片宽度一致，避免被窗口拉宽
                self.result_view.show_widget(card)
                return
            # 自定义组件（widgets/ 目录）：测试区与气泡渲染一致
            from widgets import load_module_widget
            w, werr = load_module_widget(ui, self.result_view)
            if w is not None:
                w.set_rule(rp.rule)
                try:
                    w.render(getattr(w, "state", {}), val)
                except Exception:
                    pass
                self.result_view.show_widget(w)
                return
            self.result_view.show_result(val, werr or err or "")
            return
        self.result_view.show_result(val, err)

    def _accept(self):
        try:
            r = self._build_rule()
        except Exception as e:
            self.result_view.show_result("", "JSON 解析失败：%s" % e)
            return
        # 内置/自定义模块不能同名：与其它规则（含内置）重名时拒绝保存
        name = str(r.get("name", "")).strip()
        parent = self.parent()
        others = getattr(parent, "temp_status_rules", []) if parent is not None else []
        try:
            for o in others:
                if (str(o.get("id")) != str(self._rule_id)
                        and str(o.get("name", "")).strip() == name):
                    QMessageBox.warning(self, "名称重复",
                                        "已有同名模块「%s」（内置或自定义），请换一个名称。" % name)
                    return
        except Exception:
            pass
        self.rule = r
        self.accept()


# ==================== 设置对话框 ====================
class SettingsDialog(QDialog):

    _DARK_SUBS = (
        ("#eef2f8", "#1b2130"), ("#ffffff", "#232a3a"),
        ("#d8dde8", "#39414f"), ("#3a3f55", "#d5dbe8"),
        ("#5a6178", "#aab3c5"), ("#9aa2b2", "#7f8aa0"),
        ("#f4f7fc", "#1f2634"), ("#e0e5ee", "#39414f"),
        ("#eaf1fb", "#2b3446"), ("#eaf3ff", "#2b3446"),
        ("#f7f9fc", "#1f2634"), ("#dce3ee", "#232a3a"),
        ("#e3eaf5", "#2b3446"), ("#c4dcf5", "#3a4a63"),
        ("#f0f3f8", "#2b3446"), ("#a0a8b8", "#6b7689"),
        ("#c8d2e2", "#4a5468"), ("#4a7fc8", "#7aa9e8"),
        ("#2456a0", "#9cc4ff"), ("#1a4a8a", "#8ab6ff"),
        ("#b8c0d0", "#465066"),
    )

    _tech_qss = """
        QDialog#TechSettings { background: #eef2f8; font-family: "SimHei"; }
        QLabel { color: #3a3f55; font-family: "SimHei"; font-size: 11px; }
        QGroupBox {
            border: 1px solid #d8dde8; border-radius: 8px; margin-top: 12px;
            background: #ffffff; padding: 6px 6px 6px 6px;
            font-family: "SimHei"; font-size: 11px; color: #5a6178;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 12px; padding: 0 6px;
            color: #4a7fc8; font-weight: 600; font-size: 11px;
        }
        QListWidget {
            background: #f7f9fc; border: 1px solid #d8dde8; border-radius: 6px;
            outline: none; color: #3a3f55; font-size: 11px; padding: 3px;
        }
        QListWidget::item { padding: 5px 8px; border-radius: 4px; min-height: 20px; }
        QListWidget::item:hover { background: #eaf1fb; }
        QListWidget::item:selected {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4a90e2, stop:1 #2fb8c0);
            color: #ffffff;
        }
        QListWidget::item:selected:focus { border: 1px solid #2fb8c0; }
        QScrollBar:vertical { background: transparent; width: 8px; border: none; margin: 0; }
        QScrollBar::handle:vertical { background: #c0cad8; border-radius: 4px; min-height: 26px; margin: 2px; }
        QScrollBar::handle:vertical:hover { background: #4a90e2; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        QLineEdit { background: #ffffff; color: #3a3f55; border: 1px solid #d8dde8; border-radius: 4px; padding: 3px 6px; }
        QInputDialog QLabel { font-size: 11px; }
        QInputDialog QPushButton { min-width: 60px; }
        QMenu {
            background: #ffffff; border: 1px solid #c8d2e2; border-radius: 8px;
            padding: 3px; margin: 0;
            font-family: "SimHei"; font-size: 11px; color: #3a3f55;
        }
        QMenu::item { padding: 5px 20px 5px 14px; border-radius: 4px; margin: 0 2px; }
        QMenu::item:selected {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4a90e2, stop:1 #2fb8c0);
            color: #ffffff;
        }
        QMenu::separator { height: 1px; background: #e0e5ee; margin: 3px 6px; }
        QPushButton {
            font-family: "SimHei"; font-size: 11px; color: #3a3f55;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #dce3ee);
            border: 1px solid #b8c0d0; border-radius: 5px;
            padding: 6px 12px; min-width: 56px;
        }
        QPushButton:hover {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #eaf3ff, stop:1 #c4dcf5);
            border: 1px solid #4a90e2; color: #2456a0;
        }
        QPushButton:pressed {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #c4dcf5, stop:1 #eaf3ff);
            border: 1px solid #2a6fc0; color: #1a4a8a;
            padding-top: 7px; padding-bottom: 5px;
        }
        QPushButton#primary {
            color: #ffffff; font-weight: 600;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4aa3ff, stop:1 #2fb8c0);
            border: 1px solid #2a8fc0;
        }
        QPushButton#primary:hover {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #5ab3ff, stop:1 #3fc8d0);
            border: 1px solid #1a7fc0;
        }
        QPushButton#primary:pressed {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2fb8c0, stop:1 #4aa3ff);
        }
        QPushButton:disabled { color: #a0a8b8; background: #f0f3f8; border: 1px solid #e0e5ee; }
        QToolTip { background: #ffffff; color: #3a3f55; border: 1px solid #c8d2e2; border-radius: 4px; padding: 3px 6px; }
        QMessageBox { background: #ffffff; }
        QMessageBox QLabel { color: #3a3f55; font-size: 11px; }
        QMessageBox QPushButton { min-width: 60px; }
        QCheckBox { font-family: "SimHei"; font-size: 11px; color: #3a3f55; spacing: 4px; }
    """

    def _apply_tech_style(self):
        self.setObjectName("TechSettings")
        qss = self._tech_qss
        for old, new in self._DARK_SUBS:
            qss = qss.replace(old, new)
        self.setStyleSheet(qss)

    @staticmethod
    def _is_dark_scheme():
        """根据系统配色判断是否为深色模式。"""
        try:
            c = QApplication.palette().window().color()
            return c.lightness() < 128
        except Exception:
            return False

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("oi桌宠 v%s - 设置" % APP_VERSION)
        self.setWindowIcon(_asset_icon("settings_icon.png"))
        self.setMinimumSize(730, 560)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self._apply_tech_style()
        QTimer.singleShot(0, lambda: _set_dark_titlebar(self))
        self.setFont(QFont("SimHei"))
        self.settings = dict(settings)
        self.slot_shortcuts = list(settings.get("slot_shortcuts", []))
        self.temp_pet_image = settings.get("pet_image", "")
        self.temp_pet_size = settings.get("pet_size", 75)
        self.temp_pet_opacity = settings.get("pet_opacity", 1.0)
        self._init_done = False

        # ===== 左右布局：左侧外观+库存（窄），右侧预览（宽）+ 确定/取消 =====
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ===== 左列 =====
        left_col = QVBoxLayout()
        left_col.setSpacing(5)

        # 桌宠外观
        appear_group = QGroupBox("桌宠外观")
        appear_layout = QVBoxLayout()
        img_row = QHBoxLayout()
        _btn_compact = "min-width:0; padding:3px 12px;"
        img_btn = QPushButton("更换图片")
        img_btn.setStyleSheet(_btn_compact)
        img_btn.clicked.connect(self._change_pet_image)
        img_row.addWidget(img_btn)
        self.rst_btn = QPushButton("恢复默认")
        self.rst_btn.setStyleSheet(_btn_compact)
        self.rst_btn.clicked.connect(self._reset_pet_image)
        self._style_reset_button(self.temp_pet_image)
        img_row.addWidget(self.rst_btn)
        clear_btn = QPushButton("清空插槽")
        clear_btn.setStyleSheet(_btn_compact)
        clear_btn.clicked.connect(self._clear_slots)
        img_row.addWidget(clear_btn)
        img_row.addSpacing(12)
        self.bubble_cb = QCheckBox("气泡")
        self.bubble_cb.setChecked(bool(settings.get("bubble_enabled", True)))
        self.bubble_cb.setToolTip("启用/停用气泡模块")
        img_row.addWidget(self.bubble_cb)
        img_row.addStretch()
        appear_layout.addLayout(img_row)
        # 第二/三行：气泡大小、桌宠大小（含径向菜单）各一行（上下两排），
        # 滑动条右侧显示当前档位名
        self._bubble_slider = QSlider(Qt.Horizontal)
        self._bubble_slider.setRange(0, len(UI_SCALE_OPTIONS) - 1)
        self._bubble_slider.setFixedWidth(140)
        self._bubble_slider.setValue(self._level_index(settings.get("bubble_scale", 1.0)))
        row_b = QHBoxLayout()
        row_b.setSpacing(6)
        row_b.setContentsMargins(12, 0, 0, 0)   # 与"更换图片"按钮文字左对齐（按钮 padding 12px）
        row_b.addWidget(QLabel("气泡大小"))
        row_b.addWidget(self._bubble_slider, 1)
        self._bubble_lab = QLabel()
        self._bubble_lab.setFixedWidth(40)
        row_b.addWidget(self._bubble_lab)
        appear_layout.addLayout(row_b)
        self._pet_slider = QSlider(Qt.Horizontal)
        self._pet_slider.setRange(0, len(UI_SCALE_OPTIONS) - 1)
        self._pet_slider.setFixedWidth(140)
        self._pet_slider.setValue(self._level_index(settings.get("pet_scale", 1.0)))
        row_p = QHBoxLayout()
        row_p.setSpacing(6)
        row_p.setContentsMargins(12, 0, 0, 0)   # 与"更换图片"按钮文字左对齐
        row_p.addWidget(QLabel("桌宠大小"))
        row_p.addWidget(self._pet_slider, 1)
        self._pet_lab = QLabel()
        self._pet_lab.setFixedWidth(40)
        row_p.addWidget(self._pet_lab)
        appear_layout.addLayout(row_p)
        self._bubble_slider.valueChanged.connect(self._update_size_labels)
        self._pet_slider.valueChanged.connect(self._update_size_labels)
        self._update_size_labels()
        # 记录打开时的初始档位（用于"是否修改过"判断，避免受已保存值影响）
        self._init_bubble = UI_SCALE_OPTIONS[self._bubble_slider.value()][1]
        self._init_pet = UI_SCALE_OPTIONS[self._pet_slider.value()][1]
        appear_layout.setContentsMargins(4, 2, 4, 2)
        appear_layout.setSpacing(2)
        self.settings.setdefault("button_size", 25)
        self.temp_show_tooltips = settings.get("show_tooltips", True)
        appear_group.setLayout(appear_layout)
        left_col.addWidget(appear_group)

        # 信息栏：配置气泡模块（内置 + 自定义模块列表占主要空间）
        info_group = QGroupBox("气泡模块")
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setContentsMargins(4, 1, 4, 2)
        self.temp_status_enabled = dict(settings.get("status_enabled", {}))
        self.temp_status_rules = list(settings.get("status_rules", []))
        self._hidden_builtins = set(settings.get("hidden_builtins") or [])
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(4)
        self._rules_title_label = QLabel("模块列表")
        _lbl_title = self._rules_title_label
        _lbl_title.setStyleSheet("color:#aab3c5; font-size:10px; padding-left:2px;")
        # 描述：悬停标题立即显示（QToolTip 即时弹出，白底样式与上一版一致）
        self._title_tip_text = (
            "支持类型：script 脚本 / llm 大模型 / http 请求 / clock 时钟 / "
            "static 静态文本\n"
            "内置模块与自定义模块分开管理，可启停 / 编辑 / 导出 / 导入")
        _lbl_title.setToolTip(self._title_tip_text)
        _lbl_title.installEventFilter(self)
        _lbl_title.setObjectName("_rulesTitleLabel")
        hdr_row.addWidget(_lbl_title, 1)
        for _t, _fn in [("导出", self._export_all_rules),
                        ("导入", self._import_rules)]:
            _b = QPushButton(_t)
            _b.setStyleSheet("min-width:0; padding:1px 6px; font-size:11px; color:#d5dbe8;")
            _b.setCursor(Qt.PointingHandCursor)
            _b.clicked.connect(_fn)
            hdr_row.addWidget(_b)
        info_layout.addLayout(hdr_row)
        # 两个入口：内置模块 / 自定义模块（点击切换下方列表）
        self._rules_tab = "builtin"
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._tab_builtin = QPushButton("内置模块")
        self._tab_custom = QPushButton("自定义模块")
        for _b in (self._tab_builtin, self._tab_custom):
            _b.setCheckable(True)
            _b.setCursor(Qt.PointingHandCursor)
            _b.setStyleSheet(
                "QPushButton{border:1px solid #39414f;border-radius:4px;"
                "padding:2px 10px;font-size:11px;color:#aab3c5;background:#1f2634;}"
                "QPushButton:checked{border-color:#4a90e2;color:#ffffff;"
                "background:rgba(74,144,226,80);}")
        self._tab_builtin.setChecked(True)
        self._tab_builtin.clicked.connect(lambda: self._switch_rules_tab("builtin"))
        self._tab_custom.clicked.connect(lambda: self._switch_rules_tab("custom"))
        tab_row.addWidget(self._tab_builtin)
        tab_row.addWidget(self._tab_custom)
        tab_row.addStretch()
        # 关闭所有模块：只取消所有勾选（不做"打开全部"）
        self._btn_disable_all = QPushButton("关闭所有模块")
        self._btn_disable_all.setCursor(Qt.PointingHandCursor)
        self._btn_disable_all.setToolTip("取消所有模块的启用勾选（仅关闭，不会用于开启）")
        self._btn_disable_all.setStyleSheet(
            "QPushButton{border:1px solid #39414f;border-radius:4px;"
            "padding:2px 10px;font-size:11px;color:#aab3c5;background:#1f2634;}"
            "QPushButton:hover{border-color:#4a90e2;color:#ffffff;"
            "background:rgba(74,144,226,80);}")
        self._btn_disable_all.clicked.connect(self._disable_all_rules)
        tab_row.addWidget(self._btn_disable_all)   # 居右（stretch 在前）
        info_layout.addLayout(tab_row)
        self.status_rules_list = _RuleListWidget(self._reorder_status_rule)
        self.status_rules_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        info_layout.addWidget(self.status_rules_list, 3)   # 列表占 3/4，测试框占低 1/4
        rbtn_row = QHBoxLayout()
        rbtn_row.setSpacing(6)
        for _t, _fn in [("添加", self._add_status_rule), ("编辑", self._edit_status_rule),
                        ("删除", self._del_status_rule), ("测试", self._test_status_rule)]:
            _b = QPushButton(_t)
            _b.setStyleSheet("min-width:0; padding:3px 14px;")
            _b.clicked.connect(_fn)
            rbtn_row.addWidget(_b)
        rbtn_row.addStretch()
        info_layout.addLayout(rbtn_row)
        # 模块测试结果区：占模块区低 1/4（3:1 比例，打开/测试时大小一致）
        self.rules_result_view = ResultView(self)
        self.rules_result_view.setMinimumHeight(80)
        info_layout.addWidget(self.rules_result_view, 1)
        info_group.setLayout(info_layout)
        left_col.addWidget(info_group, stretch=1)
        self._refresh_rules_list()

        left_widget = QWidget()
        left_widget.setLayout(left_col)
        left_widget.setFixedWidth(310)
        root.addWidget(left_widget)

        # ===== 右列：预览 + 确定/取消 =====
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_title = QLabel("径向菜单预览")
        right_title.setStyleSheet("color:#7aa9e8; font-weight:600; font-size:11px; padding-left:4px;")
        right_col.addWidget(right_title)
        self.preview = PreviewWidget()
        self.preview.setMinimumHeight(360)
        bs = self.settings.get("button_size", 25)
        self.preview.set_slot_size(max(32, int(bs * 1.2)))
        self.preview.set_pet_image_path(self.temp_pet_image)
        self.preview.set_pet_preview_size(max(min(self.temp_pet_size, 85), 70))
        self.preview.set_pet_opacity(self.temp_pet_opacity)
        self.preview.shortcuts_changed.connect(self._on_preview_changed)
        self.preview.pet_image_changed.connect(self._on_pet_image_changed)
        right_col.addWidget(self.preview, stretch=1)
        self._update_preview()
        self._init_done = True

        # 底部帮助/交互提示（位于确定/取消按钮下方，避免向下挤压预览）
        help_label = QLabel(
            "操作说明：\n"
            "· 径向菜单：拖入文件/网址自动添加；盘面空白处右键可添加"
            "网页链接/文件/程序/文件夹/命令；拖到按钮上替换，拖拽交换，拖出盘外删除；"
            "按钮右键可重命名/换图标/删除\n"
            "· 桌宠图标：拖入图片/GIF/WebP 直接替换，右键可恢复默认\n"
            "· 插槽：右键重命名/换图标/删除\n"
            "· 模块：支持 script 脚本 / llm 大模型 / http 请求 / clock 时钟 / "
            "static 静态文本；内置与自定义分开管理，可启停/编辑/导出/导入"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            "font-family:'SimHei'; color:#aab3c5; background:#1f2634;"
            " border:1px solid #39414f; border-radius:6px;"
            " padding:2px 6px; font-size:11px; line-height:120%;"
        )

        # 确定/取消 + 显示标签同行
        btn_layout = QHBoxLayout()
        self.cb_tooltips = QCheckBox("显示标签")
        self.cb_tooltips.setChecked(self.temp_show_tooltips)
        self.cb_tooltips.setToolTip("鼠标经过径向菜单按钮时是否弹出名称标签")
        btn_layout.addWidget(self.cb_tooltips)
        btn_layout.addStretch()
        self.version_label = QLabel("v%s" % APP_VERSION)
        self.version_label.setToolTip("oi桌宠当前版本")
        self.version_label.setStyleSheet("color:#7f8aa0; font-size:10px;")
        btn_layout.addWidget(self.version_label)
        for text, slot in [("确定", self._on_accept), ("取消", self.reject)]:
            b = QPushButton(text)
            b.setFixedWidth(90)
            b.clicked.connect(slot)
            if text == "确定":
                b.setObjectName("primary")
            btn_layout.addWidget(b)
        right_col.addLayout(btn_layout)
        right_col.addWidget(help_label)

        right_widget = QWidget()
        right_widget.setLayout(right_col)
        root.addWidget(right_widget, stretch=1)

        # 固定窗口大小，不允许缩放
        root.setSizeConstraint(QLayout.SetFixedSize)

    def _clear_slots(self):
        from PyQt5.QtWidgets import QMessageBox
        if not self.slot_shortcuts and not self.preview.slot_shortcuts:
            return
        ret = QMessageBox.warning(self, "确认清空",
                                   "清空径向菜单所有插槽，确定吗？",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.slot_shortcuts.clear()
            self.preview.clear_slots()

    def _on_preview_changed(self):
        if self._init_done:
            self.slot_shortcuts = list(self.preview.slot_shortcuts)

    def _update_preview(self):
        for p in _resolve_image_candidates(self.settings.get("pet_image", "")):
            if p and os.path.exists(p):
                self.preview.load_pet_image(p)
                break
        self.preview.set_slots(self.slot_shortcuts)

    def _change_pet_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择桌宠图片", "",
                                               "图片 (*.png *.ico *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*.*)")
        if path:
            self.temp_pet_image = path
            self.preview.set_pet_image_path(path)
            self.preview.load_pet_image(path)

    def _reset_pet_image(self):
        # 恢复默认：在 oi.png / yxm.webp 两个默认图标间循环
        nxt = _next_default_pet_image(self.temp_pet_image)
        self.temp_pet_image = nxt
        self.preview.set_pet_image_path(nxt)
        path = _default_pet_image_abs(nxt)
        self.preview.load_pet_image(path if path and os.path.exists(path) else "")
        self._style_reset_button(nxt)

    def _style_reset_button(self, path):
        """恢复默认按钮：只切换有无底色（yxm.webp 用"确定"按钮的底色），大小风格不变。"""
        try:
            if (_same_path(path, _DEFAULT_PET_IMAGES[1])
                    or _same_path(path, _default_pet_image_abs(_DEFAULT_PET_IMAGES[1]))):
                self.rst_btn.setStyleSheet(
                    "min-width:0; padding:3px 12px; color:#ffffff; font-weight:600;"
                    "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    " stop:0 #4aa3ff, stop:1 #2fb8c0);"
                    "border:1px solid #2a8fc0;")
            else:
                self.rst_btn.setStyleSheet("min-width:0; padding:3px 12px;")
        except Exception:
            pass

    def _on_pet_image_changed(self, path):
        self.temp_pet_image = path
        self._style_reset_button(path)

    def eventFilter(self, obj, ev):
        """模块列表标题悬停：立即显示注释（QToolTip 即时弹出，样式见全局 QSS）。"""
        try:
            if obj is getattr(self, "_rules_title_label", None):
                from PyQt5.QtCore import QEvent as _QEv
                if ev.type() == _QEv.Enter:
                    from PyQt5.QtWidgets import QToolTip as _QT
                    _QT.showText(
                        obj.mapToGlobal(QPoint(0, obj.height() + 2)),
                        self._title_tip_text)
                    return True   # 立即显示，屏蔽系统延迟 tooltip
                if ev.type() == _QEv.Leave:
                    from PyQt5.QtWidgets import QToolTip as _QT
                    _QT.hideText()
                    return False
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _level_index(self, v):
        """档位值 -> 下拉/滑动条索引（标准 1.0 起步）。"""
        try:
            v = float(v or 1.0)
        except Exception:
            v = 1.0
        for i, (_n, _vv) in enumerate(UI_SCALE_OPTIONS):
            if abs(_vv - v) < 0.001:
                return i
        return 0

    def _update_size_labels(self):
        try:
            self._bubble_lab.setText(UI_SCALE_OPTIONS[self._bubble_slider.value()][0])
            self._pet_lab.setText(UI_SCALE_OPTIONS[self._pet_slider.value()][0])
        except Exception:
            pass

    def _on_accept(self):
        self.settings["slot_shortcuts"] = self.slot_shortcuts
        self.settings["pet_image"] = self.temp_pet_image
        self.settings["pet_size"] = self.temp_pet_size
        self.settings["pet_opacity"] = self.temp_pet_opacity
        self.settings["show_tooltips"] = self.cb_tooltips.isChecked()
        self.settings["status_rules"] = self.temp_status_rules
        self.settings["status_custom_items"] = []
        self.settings["hidden_builtins"] = sorted(self._hidden_builtins)
        # 情绪启用状态跟随内置情绪规则
        mood_rule = next((r for r in self.temp_status_rules
                          if r.get("builtin") == "mood"), None)
        self.settings["mood_enabled"] = bool(mood_rule.get("enabled", True)) if mood_rule else True
        self.settings["bubble_enabled"] = self.bubble_cb.isChecked()
        # 气泡 / 桌宠 两档尺寸
        _new_b = UI_SCALE_OPTIONS[self._bubble_slider.value()][1]
        _new_p = UI_SCALE_OPTIONS[self._pet_slider.value()][1]
        # 与"打开设置时的初始档位"对比，识别是否修改过
        _b_changed = abs(getattr(self, "_init_bubble", 1.0) - _new_b) > 0.001
        _p_changed = abs(getattr(self, "_init_pet", 1.0) - _new_p) > 0.001
        self.settings["bubble_scale"] = _new_b
        self.settings["pet_scale"] = _new_p
        self.accept()
        # 档位变化由 GravityPet._apply_pet_settings 在设置窗口关闭后热更新；
        # 这里不再要求用户手动重启。

    def _visible_rule_indices(self):
        """当前 tab 下可见规则在 temp_status_rules 中的索引列表（按数组顺序）。"""
        if self._rules_tab == "builtin":
            return [i for i, r in enumerate(self.temp_status_rules) if r.get("builtin")]
        return [i for i, r in enumerate(self.temp_status_rules) if not r.get("builtin")]

    def _refresh_view_order(self):
        """重建当前 tab 的列表视图顺序（数组顺序；已有视图顺序时保持）。"""
        base = self._visible_rule_indices()
        # 新增/删除后：保留仍在的项的原视图相对顺序，追加新增项
        old = getattr(self, "_view_order", {}).get(self._rules_tab, [])
        old_set = set(old)
        kept = [i for i in old if i in base]
        added = [i for i in base if i not in old_set]
        if not hasattr(self, "_view_order"):
            self._view_order = {}
        self._view_order[self._rules_tab] = kept + added

    def _switch_rules_tab(self, tab):
        self._rules_tab = tab
        self._tab_builtin.setChecked(tab == "builtin")
        self._tab_custom.setChecked(tab == "custom")
        self._refresh_view_order()
        self._refresh_rules_list()

    def _refresh_rules_list(self):
        self.status_rules_list.clear()
        self._refresh_view_order()
        self._view_indices = list(self._view_order.get(self._rules_tab, []))
        for vi, idx in enumerate(self._view_indices):
            r = self.temp_status_rules[idx]
            row = _RuleRow(self.status_rules_list, vi, r, dlg=self)
            row.cb.stateChanged.connect(
                lambda s, i=idx: self._set_rule_enabled(i, s == Qt.Checked))
            it = QListWidgetItem()
            it.setSizeHint(QSize(200, 26))
            self.status_rules_list.addItem(it)
            self.status_rules_list.setItemWidget(it, row)

    def _current_rule_index(self):
        """列表当前行对应的 temp_status_rules 真实索引；无选中返回 -1。"""
        row = self.status_rules_list.currentRow()
        if 0 <= row < len(self._view_indices):
            return self._view_indices[row]
        return -1

    def _set_rule_enabled(self, idx, enabled):
        if not (0 <= idx < len(self.temp_status_rules)):
            return
        r = self.temp_status_rules[idx]
        r["enabled"] = bool(enabled)

    def _disable_all_rules(self):
        """关闭所有模块：取消全部启用勾选（仅关闭，不做打开）。"""
        try:
            ret = QMessageBox.question(
                self, "关闭所有模块", "确定关闭所有模块（取消全部启用勾选）？")
            if ret != QMessageBox.Yes:
                return
            for r in self.temp_status_rules:
                r["enabled"] = False
            self._refresh_rules_list()
        except Exception:
            pass

    def _reorder_status_rule(self, src, dst):
        """拖拽排序：只调整当前 tab 的列表显示顺序，不影响气泡顺序。"""
        try:
            order = list(self._view_order.get(self._rules_tab, []))
            n = len(order)
            if not (0 <= src < n):
                return
            dst = max(0, min(dst, n - 1))
            if src == dst:
                return
            order.insert(dst, order.pop(src))
            self._view_order[self._rules_tab] = order
            self._refresh_rules_list()
            self.status_rules_list.setCurrentRow(dst)
        except Exception:
            pass

    def _add_status_rule(self):
        def done(r):
            self.temp_status_rules.append(r)
            self._refresh_rules_list()
        self._open_rule_dialog(None, done)

    def _edit_status_rule(self):
        idx = self._current_rule_index()
        if 0 <= idx < len(self.temp_status_rules):
            def done(r):
                self.temp_status_rules[idx] = r
                self._refresh_rules_list()
            self._open_rule_dialog(self.temp_status_rules[idx], done)

    def _open_rule_dialog(self, rule, on_done):
        """非模态打开模块编辑窗口：同一时间只开一个，已存在则前置；关闭后回调结果。"""
        ref = _RULE_DIALOG_REF[0]
        old = ref() if ref else None
        if old is not None and isinstance(old, QWidget) and not old.isHidden():
            try:
                old.raise_()
                old.activateWindow()
            except Exception:
                pass
            return
        dlg = RuleDialog(self, rule)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        _RULE_DIALOG_REF[0] = weakref.ref(dlg)

        def _fin(result):
            try:
                if result == QDialog.Accepted and dlg.rule:
                    on_done(dlg.rule)
            except Exception:
                pass
        dlg.finished.connect(_fin)
        dlg.show()

    def _del_status_rule(self):
        idx = self._current_rule_index()
        if not (0 <= idx < len(self.temp_status_rules)):
            return
        r = self.temp_status_rules[idx]
        name = r.get("name", "模块")
        # 内置模块删除前确认（删除后不再自动加回）；自定义模块同样确认
        ret = QMessageBox.question(
            self, "删除模块", "确定删除模块「%s」？\n删除后不可恢复。" % name)
        if ret != QMessageBox.Yes:
            return
        if r.get("builtin"):
            self._hidden_builtins.add(r.get("id"))
        self.temp_status_rules.pop(idx)
        self._refresh_rules_list()

    def _export_one_rule(self, view_row):
        """导出单个模块（右键菜单）：view_row 为列表显示行号。"""
        try:
            if not (0 <= view_row < len(self._view_indices)):
                return
            rule = self.temp_status_rules[self._view_indices[view_row]]
            path, _ = QFileDialog.getSaveFileName(
                self, "导出模块", "%s.json" % rule.get("name", "模块"), "JSON 文件 (*.json)")
            if not path:
                return
            if not path.lower().endswith(".json"):
                path += ".json"
            data = {"status_rules": [rule]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出完成",
                                    "已导出模块“%s”到：\n%s"
                                    % (rule.get("name", "模块"), path))
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _export_all_rules(self):
        """导出全部模块列表到 JSON 文件（备份/分享）。"""
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "导出全部模块", "oi桌宠_模块.json", "JSON 文件 (*.json)")
            if not path:
                return
            if not path.lower().endswith(".json"):
                path += ".json"
            data = {"status_rules": self.temp_status_rules}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出完成",
                                    "已导出 %d 个模块到：\n%s"
                                    % (len(self.temp_status_rules), path))
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _import_rules(self):
        """从 JSON 文件批量导入模块（支持多选，追加并按 id 去重）。"""
        try:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "导入模块（可多选）", "", "JSON 文件 (*.json)")
            if not paths:
                return
            existing = {r.get("id") for r in self.temp_status_rules}
            added = 0
            for path in paths:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rules = data.get("status_rules") if isinstance(data, dict) else data
                if not isinstance(rules, list):
                    raise ValueError("文件格式不正确：应为 {\"status_rules\": [...]}")
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id") or ("r%d" % int(time.time() * 1000) + str(added))
                    if rid in existing:
                        continue
                    nr = dict(r)
                    # 导入的模块一律归为自定义模块（去掉内置标记，
                    # 避免与内置模块冲突/进错列表）
                    nr.pop("builtin", None)
                    self.temp_status_rules.append(nr)
                    existing.add(rid)
                    added += 1
            self._refresh_rules_list()
            # 导入后自动切到自定义模块列表展示
            self._switch_rules_tab("custom")
            QMessageBox.information(
                self, "导入完成",
                ("成功导入 %d 个模块（已加入自定义模块）" % added) if added
                else "没有新增模块（导入的文件中都已存在）")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _test_status_rule(self):
        idx = self._current_rule_index()
        if not (0 <= idx < len(self.temp_status_rules)):
            self.rules_result_view.show_result("", "请先选择一个模块")
            return
        rule = self.temp_status_rules[idx]
        name = rule.get("name", "规则")
        self.rules_result_view.show_result("", "%s：测试中…" % name)
        bridge = _RuleTestBridge(self)
        bridge.done.connect(lambda val, err, rp: self._on_rule_test_done(name, val, err, rp))

        def work():
            val, err, rp = "", "", None
            try:
                from status_monitor import RuleProvider
                rp = RuleProvider(rule)
                val = rp.collect()
            except Exception as e:
                err = str(e)
            try:
                bridge.done.emit(val, err, rp)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_rule_test_done(self, name, val, err, rp):
        ui = rp._source().get("ui") if rp is not None else ""
        if ui:
            if ui == "pomodoro":
                from h5_cards import PomodoroCard
                card = PomodoroCard(state=rp.state)
                card.setFixedWidth(184)
                self.rules_result_view.show_widget(card)
                return
            from widgets import load_module_widget
            w, werr = load_module_widget(ui, self.rules_result_view)
            if w is not None:
                w.set_rule(rp.rule)
                try:
                    w.render(getattr(w, "state", {}), val)
                except Exception:
                    pass
                self.rules_result_view.show_widget(w)
                return
            self.rules_result_view.show_result("%s：%s" % (name, val), werr or err)
            return
        self.rules_result_view.show_result("%s：%s" % (name, val), err)



# ==================== 桌宠主窗口 ====================
class GravityPet(QWidget):

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.pet_config = config.get("pet", {})
        self.settings = load_settings()
        # 桌宠本体大小 = 用户设置值 × 桌宠档位（设置窗口"桌宠大小"滑动条）
        from widgets import kit as _kit
        self._applied_bubble_scale = _kit.bubble_scale()
        self._applied_pet_scale = _kit.pet_scale()
        self.pet_size = max(40, int(
            self.settings.get("pet_size", self.pet_config.get("size", 75))
            * _kit.pet_scale()))
        self.pet_opacity = self.settings.get("pet_opacity", self.pet_config.get("opacity", 1.0))
        self.is_dragging = False
        self.drag_offset = QPoint(0, 0)
        self._press_pos = None
        self._did_drag = False
        self._is_pressed = False
        self.anim_type = None
        self._elastic_active = False
        self._elastic_timer = QElapsedTimer()
        self.breath_timer = QElapsedTimer()
        self.breath_timer.start()
        self._current_scale = 1.0
        self._current_rotation = 0.0
        self._snap_rotation = 0.0
        # 弹性缩放叠加层（1.0=无效果，叠加在呼吸动画之上）
        self._elastic_mod = 1.0
        # 按下摇晃动画
        self._wobble_rot = 0.0
        self._wobble_x = 0.0
        self._wobble_y = 0.0
        self.snapped_edge = None
        self.snap_anim_timer = QElapsedTimer()
        self.snap_target_x = self.snap_target_y = 0
        self.snap_start_x = self.snap_start_y = 0
        self.snap_target_rotation = self.snap_start_rotation = 0.0
        self.snap_duration = 0.1
        # 从边缘回正的旋转过渡动画
        self._unsnap_active = False
        self._unsnap_timer = QElapsedTimer()
        self._unsnap_start_rot = 0.0
        self._unsnap_duration = 0.15
        self._unsnap_start_pos = None  # 回正动画起始位置
        self._pending_unsnap_rot = None
        self.is_fullscreen = False
        self._fs_count = 0
        self.original_pixmap = None
        self._gif_movie = None
        self._gif_frame_pm = None
        self._webp_anim = None
        self._webp_frame_pm = None
        self._last_pet_image = None
        self._shadow_mask_pm = None  # 图标阴影遮罩缓存
        self._shadow_mask_key = None
        self._setup_window()
        self._load_image()
        self.radial_menu = RadialMenu(self)
        # 布局版气泡（Qt 布局引擎排布内容）为默认实现
        self.status_bubble = StatusBubbleLayout(self)
        self._mood_bubble = MoodBubble(self)
        self._mood_timer = QTimer(self)
        self._mood_timer.timeout.connect(self._maybe_show_mood)
        self._mood_timer.start(20000)
        self._last_interact = time.monotonic()
        self._mood_enabled = self.settings.get("mood_enabled", True)
        self.status_bubble.set_config(self.settings.get("status_enabled"), self.settings.get("status_custom_items"),
                                      self.settings.get("status_rules"))
        self._mood_enabled = self.settings.get("mood_enabled", True)
        self.bubble_enabled = self.settings.get("bubble_enabled", True)
        # 悬停 1 秒后才弹状态气泡
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._maybe_show_bubble)
        self._click_suppress = False  # 点击后抑制气泡，直到鼠标离开
        self._bubble_was_over = False  # 上一帧光标是否在桌宠上（菜单展开轮询）
        self._drop_hover_pet = False  # 外部拖入悬停在桌宠图片上
        self._drop_glow_pet = 0.0     # 拖入高光进度 0..1
        self._drop_glow_ts = 0.0
        self._halo_mask_pm = None
        self._halo_mask_key = None
        self._drop_toast = None     # 拖入替换桌宠图片的提示气泡
        self.setAcceptDrops(True)  # 支持拖入图片替换桌宠图标
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._update_frame)
        self.frame_timer.setTimerType(Qt.PreciseTimer)
        self.frame_timer.start(16)
        self.fs_timer = QTimer(self)
        self.fs_timer.timeout.connect(self._check_fullscreen)
        self.fs_timer.start(500)

    def _pet_window_icon(self):
        """任务管理器/窗口图标统一用 oi.png 图案"""
        for p in ["assets/oi.png", "oi.png",
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "oi.png")]:
            if p and os.path.exists(p):
                return QIcon(p)
        return QIcon()

    def _show_drop_toast(self, text, gpos):
        self._close_drop_toast()
        self._drop_toast = Toast(text, gpos, duration=0)

    def _close_drop_toast(self):
        if getattr(self, '_drop_toast', None) is not None:
            try:
                self._drop_toast.close()
            except Exception:
                pass
            self._drop_toast = None

    def _drop_image_path(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in (
                    ".png", ".ico", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                return p
        return None

    def dragEnterEvent(self, event):
        if self._drop_image_path(event):
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drop_image_path(event):
            if not self._drop_hover_pet:
                self._drop_hover_pet = True
                self._show_drop_toast("松手替换桌宠图片", self.mapToGlobal(event.pos()))
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_hover_pet = False
        self._close_drop_toast()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        path = self._drop_image_path(event)
        self._drop_hover_pet = False
        self._close_drop_toast()
        if not path:
            Toast("仅支持图片/GIF/WebP 格式", self.mapToGlobal(event.pos()))
            event.ignore()
            return
        # 延后到拖放事件结束后再弹确认，避免拖放过程中嵌套事件循环导致崩溃
        self._pending_drop = (path, self.mapToGlobal(event.pos()))
        QTimer.singleShot(0, self._process_pending_drop)
        event.acceptProposedAction()

    def _process_pending_drop(self):
        pending = getattr(self, '_pending_drop', None)
        if not pending:
            return
        self._pending_drop = None
        path, gpos = pending
        pop = ConfirmPopup("将桌宠图片替换为：%s？" % os.path.basename(path))
        if pop.exec_(gpos):
            self.settings["pet_image"] = path
            save_settings(self.settings)
            self._load_image()
            if self.status_bubble.isVisible():
                self.status_bubble.hide_animated()

    def _setup_window(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowOpacity(self.pet_opacity)
        self.setFixedSize(self.pet_size + PET_SHADOW_MARGIN * 2,
                          self.pet_size + PET_SHADOW_MARGIN * 2)
        self.setWindowIcon(self._pet_window_icon())
        s = QApplication.primaryScreen().availableGeometry()
        self.move(s.x() + (s.width() - self.width()) // 2,
                  s.y() + (s.height() - self.height()) // 2)

    def _load_image(self):
        # 清理旧的 QMovie / webp 动图
        if self._gif_movie is not None:
            self._gif_movie.stop()
            self._gif_movie = None
        if self._webp_anim is not None:
            self._webp_anim.stop()
            self._webp_anim = None
        self._gif_frame_pm = None
        self._webp_frame_pm = None
        image_path = self.settings.get("pet_image", "") or self.pet_config.get("image", "assets/oi.png")
        self._last_pet_image = self.settings.get("pet_image", "")
        for p in _resolve_image_candidates(image_path):
            if p and os.path.exists(p):
                ext = os.path.splitext(p)[1].lower()
                # .webp 动图用 Pillow 播放
                if ext == ".webp":
                    anim = _WebpAnim(p, self.pet_size, self._on_webp_frame, self)
                    if anim.is_valid():
                        self._webp_anim = anim
                        self._webp_frame_pm = None
                        self.original_pixmap = None
                        anim.start()
                        return
                # .gif 用 QMovie 动画播放
                elif ext == ".gif":
                    movie = QMovie(p)
                    if movie.isValid():
                        movie.frameChanged.connect(self._on_gif_frame)
                        self._gif_movie = movie
                        self._gif_frame_pm = None
                        self.original_pixmap = None  # 静态 pixmap 占位为 None
                        movie.start()
                        return
                else:
                    pm = QPixmap(p)
                    if not pm.isNull():
                        self.original_pixmap = pm.scaled(self.pet_size, self.pet_size,
                                                          Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        return

    def _on_gif_frame(self):
        if self._gif_movie is not None:
            raw = self._gif_movie.currentPixmap()
            if raw and not raw.isNull():
                self._gif_frame_pm = raw.scaled(self.pet_size, self.pet_size,
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.update()

    def _on_webp_frame(self):
        if self._webp_anim is not None:
            self._webp_frame_pm = self._webp_anim.current_pixmap()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.translate(self.width() / 2.0, self.height() / 2.0)
        # 按下摇晃（与呼吸旋转叠加）
        if abs(self._wobble_x) > 0.05 or abs(self._wobble_y) > 0.05:
            p.translate(self._wobble_x, self._wobble_y)
        rot = self._current_rotation + self._wobble_rot
        if abs(rot) > 0.01:
            p.rotate(rot)
        # 获取当前绘制帧：GIF 优先，否则静态 pixmap
        pm = None
        gif_active = self._gif_movie is not None
        if gif_active:
            pm = self._gif_frame_pm
        if (pm is None or pm.isNull()) and self._webp_anim is not None:
            pm = self._webp_frame_pm
        if pm is None or pm.isNull():
            pm = self.original_pixmap
        if pm is None or pm.isNull():
            # 没有有效的帧可显示；占位渲染（静图/GIF 均未准备好）
            if gif_active:
                # GIF 正在解码第一帧，暂时不绘制任何内容
                pass
            else:
                r = self.pet_size / 2.0
                p.setBrush(Qt.blue)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(0, 0), r, r)
                p.setPen(Qt.white)
                p.drawText(QRectF(-r, -r, self.pet_size, self.pet_size), Qt.AlignCenter, "(???)")
        else:
            # 图标阴影：呼吸/放大时保持原始尺寸；按下缩小时跟随缩小（大小不超过图案）
            shadow_scale = min(1.0, self._current_scale)
            shadow = self._shadow_mask(pm)
            if shadow_scale < 0.999:
                p.save()
                p.scale(shadow_scale, shadow_scale)
            for k in range(3):
                a = 22 - k * 7
                if a < 1:
                    continue
                offx = 1 + k
                offy = 2 + k
                p.setOpacity(a / 255.0)
                p.drawPixmap(int(-pm.width() // 2 + offx), int(-pm.height() // 2 + offy),
                             pm.width(), pm.height(), shadow)
            p.setOpacity(1.0)
            if shadow_scale < 0.999:
                p.restore()
            p.scale(self._current_scale, self._current_scale)
            # 拖入高光：沿图案像素外边缘的发光描边（待修改状态，呼吸闪烁）
            if self._drop_glow_pet > 0.01:
                halo = self._pixel_halo(pm)
                pulse = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(time.monotonic() * 4 * math.pi))
                p.setOpacity(min(1.0, self._drop_glow_pet * pulse * 1.15))
                p.drawPixmap(int(-pm.width() // 2), int(-pm.height() // 2), halo)
                p.setOpacity(1.0)
            p.drawPixmap(-pm.width() // 2, -pm.height() // 2, pm)
        p.end()

    def enterEvent(self, event):
        # 鼠标移到桌宠图标上，0.1 秒后弹出状态气泡
        self._hover_timer.start(100)
        # 回到桌宠悬停：取消气泡的消失倒计时
        try:
            self.status_bubble._auto_hide_timer.stop()
            self.status_bubble._hide_timer.stop()
        except Exception:
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_timer.stop()
        self._click_suppress = False  # 离开后重新允许悬停弹出
        # 延迟隐藏：鼠标移到气泡上（钉住/悬停气泡）时不消失
        self.status_bubble.start_hide_countdown()
        super().leaveEvent(event)

    def _maybe_show_bubble(self):
        if not getattr(self, "bubble_enabled", True):
            return
        # 菜单展开时也显示气泡（气泡会上移到菜单上方不被遮挡）
        if (self._cursor_over_pet() and not self.is_dragging and not self._click_suppress):
            self.status_bubble.show_near()

    def _cursor_over_pet(self):
        """光标是否落在桌宠图标圆形区域内（与图标一致，不外扩，避免未碰到就触发）"""
        try:
            gp = QCursor.pos()
            local = self.mapFromGlobal(gp)
            # 用图标圆形命中区（比图标大 48.5%，与右键/拖拽判定一致）
            return self._pet_circle_contains(local)
        except Exception:
            return self.underMouse()

    def _touch_interact(self):
        self._last_interact = time.monotonic()

    def show_h5_cards(self, cards):
        """H5 拓展基础：在状态气泡内显示一组卡片（后续功能开发入口）。"""
        try:
            b = self.status_bubble
            b.show_cards(cards)
            if not b.isVisible():
                b._relayout()
                b._place()
                b.show()
                b.raise_()
        except Exception:
            import traceback
            traceback.print_exc()

    def _maybe_show_mood(self):
        """待机情绪气泡：交互空闲后随机冒出鼓励语句"""
        if not self._mood_rule_enabled():
            return
        if self.is_dragging or self.is_fullscreen or not self.isVisible():
            return
        if self.status_bubble.isVisible() or self.radial_menu.is_visible_state:
            return
        idle = time.monotonic() - getattr(self, '_last_interact', time.monotonic())
        if idle < 60 or random.random() > 0.5:
            return
        self._mood_bubble.show_phrase()

    def _mood_rule_enabled(self):
        """情绪是否启用：跟随规则列表里的内置"情绪"规则。"""
        try:
            for p in self.status_bubble._rules:
                if p.rule.get("builtin") == "mood":
                    return bool(p.rule.get("enabled", True))
        except Exception:
            pass
        return bool(getattr(self, '_mood_enabled', True))

    def _shadow_mask(self, pm):
        """把图案 alpha 保留、颜色改为黑色，作为投影遮罩（按 pixmap 缓存）"""
        key = pm.cacheKey()
        if self._shadow_mask_key != key:
            img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
            mp = QPainter(img)
            mp.setCompositionMode(QPainter.CompositionMode_SourceIn)
            mp.fillRect(img.rect(), QColor(0, 0, 0))
            mp.end()
            self._shadow_mask_pm = QPixmap.fromImage(img)
            self._shadow_mask_key = key
        return self._shadow_mask_pm

    def _pixel_halo(self, pm):
        """沿图案像素外边缘外扩一圈的高光描边（按帧缓存）"""
        key = pm.cacheKey()
        if self._halo_mask_key != key or self._halo_mask_pm is None:
            self._halo_mask_pm = _make_pixel_halo(pm)
            self._halo_mask_key = key
        return self._halo_mask_pm

    def _update_frame(self):
        # 拖入高光过渡（淡入淡出，时间驱动）
        now_m = time.monotonic()
        dt_m = min(0.05, max(0.0, now_m - self._drop_glow_ts)) if self._drop_glow_ts else 0.0
        self._drop_glow_ts = now_m
        tg = 1.0 if self._drop_hover_pet else 0.0
        if abs(self._drop_glow_pet - tg) > 0.001:
            rate_g = 8.0 if tg > self._drop_glow_pet else 5.0
            self._drop_glow_pet += (tg - self._drop_glow_pet) * min(1.0, dt_m * rate_g)
            if abs(self._drop_glow_pet - tg) < 0.001:
                self._drop_glow_pet = tg
        # 气泡跟随桌宠：每帧同步位置（吸附动画/拖动时都平滑跟走）
        try:
            if self.status_bubble.isVisible() and not self.status_bubble._anim_dir:
                self.status_bubble._place()
        except Exception:
            pass
        # 独立弹出气泡每帧跟随桌宠（无论主气泡是否可见/菜单是否展开）
        try:
            self.status_bubble._place_popup()
        except Exception:
            pass
        # 菜单展开时：菜单窗口覆盖桌宠会吞掉鼠标事件，用光标坐标轮询悬停
        try:
            if (self.radial_menu.is_visible_state and not self.is_dragging
                    and not self._click_suppress):
                over = self._cursor_over_pet()
                if over:
                    if not self._hover_timer.isActive():
                        self._hover_timer.start(100)
                    # 回到桌宠悬停：取消气泡的消失倒计时
                    self.status_bubble._auto_hide_timer.stop()
                    self.status_bubble._hide_timer.stop()
                else:
                    self._hover_timer.stop()
                    # 只在"刚离开"的瞬间启动一次倒计时，避免每帧重置导致永不消失
                    if getattr(self, '_bubble_was_over', False):
                        self.status_bubble.start_hide_countdown()
                self._bubble_was_over = over
            else:
                self._bubble_was_over = False
        except Exception:
            pass
        # 悬停弹出兜底：光标在桌宠上且气泡隐藏时恢复悬停计时。
        # 修复"点✕关闭后无法再弹出"——关闭后光标已在桌宠上，enterEvent
        # 不会再次触发，悬停计时器永不重启；这里用帧轮询兜底（关闭后
        # _click_suppress 为 True，需鼠标离开桌宠（leaveEvent 清除）才会恢复）
        try:
            if (not self.is_dragging and not self._click_suppress
                    and self.status_bubble is not None
                    and not self.status_bubble.isVisible()
                    and self._cursor_over_pet()):
                if not self._hover_timer.isActive():
                    self._hover_timer.start(100)
                self.status_bubble._auto_hide_timer.stop()
                self.status_bubble._hide_timer.stop()
        except Exception:
            pass
        # ── 呼吸基底（永远运行，所有效果叠加其上）──
        t_br = self.breath_timer.elapsed() / 1000.0
        p_br = (t_br % BREATH_PERIOD) / BREATH_PERIOD
        breath_scale = 1.0 + BREATH_SCALE * (0.5 + 0.5 * math.sin(p_br * math.pi * 2))

        # ── 旋转：snap / unsnap / 呼吸 ──
        if self._unsnap_active and self.anim_type != "snap":
            et_unsnap = min(self._unsnap_timer.elapsed() / 1000.0 / self._unsnap_duration, 1.0)
            eased_unsnap = ease_out_back_strong(et_unsnap)
            self._current_rotation = self._unsnap_start_rot * (1.0 - eased_unsnap)
            if self._unsnap_start_pos is not None:
                cur = self.pos()
                sx, sy = self._unsnap_start_pos.x(), self._unsnap_start_pos.y()
                cx, cy = cur.x(), cur.y()
                self.move(int(sx + (cx - sx) * eased_unsnap), int(sy + (cy - sy) * eased_unsnap))
            if et_unsnap >= 1.0:
                self._unsnap_active = False
                self._current_rotation = 0.0
                self._unsnap_start_pos = None
        elif self.anim_type == "snap":
            t_snap = ease_out_back(min(self.snap_anim_timer.elapsed() / 1000.0 / self.snap_duration, 1.0))
            self.move(int(self.snap_start_x + (self.snap_target_x - self.snap_start_x) * t_snap),
                      int(self.snap_start_y + (self.snap_target_y - self.snap_start_y) * t_snap))
            self._current_rotation = self.snap_start_rotation + (self.snap_target_rotation - self.snap_start_rotation) * t_snap
            # 菜单展开时：菜单窗口跟随吸附移动并按新位置重排
            if self.radial_menu.is_visible_state:
                try:
                    self.radial_menu._reposition_geometry(self.radial_menu._margin,
                                                          self.radial_menu._margin * 2)
                    if not self.radial_menu._animating:
                        self.radial_menu.reflow()
                except Exception:
                    pass
            if t_snap >= 1.0:
                self.anim_type = None
                self._current_rotation = self.snap_target_rotation
        else:
            self._current_rotation = self._snap_rotation + 1.5 * math.sin(t_br / BREATH_PERIOD * math.pi * 2 + math.pi / 3)

        # ── 弹性缩放叠加层（按下→PRESS_SCALE，松手→过冲→1.0）──
        if self._elastic_active:
            et_el = min(self._elastic_timer.elapsed() / 1000.0 / ELASTIC_DURATION, 1.0)
            target = float(self._elastic_target)
            if et_el < 1.0:
                # 带过冲的弹性缓动：目标是1.0时产生10%放大过冲
                e_raw = ease_out_back_strong(et_el)
                if abs(target - 1.0) < 0.01:
                    # 松手恢复：过冲
                    decay = math.exp(-et_el * ELASTIC_DECAY)
                    e = e_raw * decay * (1 + 0.15 * math.sin(et_el * ELASTIC_CYCLES * math.pi * 2))
                    self._elastic_mod = self._elastic_start + (1.12 - self._elastic_start) * min(e, 1.0)
                    # 过冲后回落到1.0
                    if e < 1.0 and et_el > 0.3:
                        self._elastic_mod = 1.0 + (self._elastic_mod - 1.0) * max(0.0, 1.0 - (et_el - 0.3) / 0.7)
                else:
                    # 按下：平滑过渡到目标
                    self._elastic_mod = self._elastic_start + (target - self._elastic_start) * ease_out_cubic(et_el)
            else:
                self._elastic_mod = target
                self._elastic_active = False
        elif self.is_dragging:
            self._elastic_mod = PRESS_SCALE
            t = time.monotonic() * 30.0
            self._wobble_rot = 5.0 * math.sin(t)
            self._wobble_x = 0.5 * math.sin(t * 1.3)
            self._wobble_y = 0.3 * math.sin(t * 0.9 + 1.0)
        elif self._is_pressed:
            self._elastic_mod = PRESS_SCALE
            t = time.monotonic() * 30.0
            self._wobble_rot = 5.0 * math.sin(t)
            self._wobble_x = 0.5 * math.sin(t * 1.3)
            self._wobble_y = 0.3 * math.sin(t * 0.9 + 1.0)
        else:
            # 摇晃衰减
            self._wobble_rot *= 0.85
            self._wobble_x *= 0.85
            self._wobble_y *= 0.85

        # ── 最终组合 ──
        self._current_scale = breath_scale * self._elastic_mod
        self.update()

    def trigger_elastic(self, action):
        """触发弹性缩放叠加层动画"""
        self._elastic_active = True
        self._elastic_start = float(self._elastic_mod)
        self._elastic_target = PRESS_SCALE if action in ('open', 'close') else float(action)
        self._elastic_timer.start()
        if action in ('open', 'close'):
            self._elastic_target = 1.0  # 松手恢复到1.0（叠加在呼吸上）

    def trigger_elastic_press(self):
        """按下：弹性层过渡到PRESS_SCALE，先立刻给部分反馈"""
        self._elastic_active = True
        self._elastic_start = 0.95  # 立刻部分缩小，保证快速点击也有反馈
        self._elastic_target = PRESS_SCALE
        self._elastic_timer.start()

    def _check_fullscreen(self):
        # 全屏应用（如B站视频全屏）时隐藏桌宠，退出全屏自动恢复；
        # 桌面/任务栏/Alt+Tab 预览已被 is_foreground_fullscreen 排除，不会误隐藏。
        if self.is_dragging:
            return
        try:
            fs = is_foreground_fullscreen()
        except Exception:
            fs = False
        if fs != self.is_fullscreen:
            self._fs_count += 1
            if self._fs_count >= 3:
                self._fs_count = 0
                self.is_fullscreen = fs
                if fs:
                    self.hide()
                    if self.radial_menu.is_visible_state:
                        self.radial_menu.hide_menu(animate=False)
                else:
                    self.show()
                    self.raise_()
        else:
            self._fs_count = 0

    def _check_edge_snap(self):
        s = QApplication.primaryScreen().availableGeometry()
        pos = self.pos()
        m = PET_SHADOW_MARGIN
        w = self.pet_size  # 桌宠图标实际尺寸（阴影留白偏移）
        ld = pos.x() + m - s.x()
        rd = s.x() + s.width() - (pos.x() + m + w)
        td = pos.y() + m - s.y()
        bd = s.y() + s.height() - (pos.y() + m + w)
        md = min(ld, rd, td, bd)
        if md > EDGE_SNAP_THRESHOLD:
            self.snapped_edge = None
            self._snap_rotation = 0.0
            return False
        sp = int(w * EDGE_SHOW_RATIO)
        if md == ld:
            e, tx, ty, rot = "left", s.x() - m - w + sp, pos.y(), EDGE_TILT_ANGLE
        elif md == rd:
            e, tx, ty, rot = "right", s.x() + s.width() - sp - m, pos.y(), -EDGE_TILT_ANGLE
        elif md == td:
            e, tx, ty, rot = "top", pos.x(), s.y() - m - w + sp, -EDGE_TILT_ANGLE
        else:
            e, tx, ty, rot = "bottom", pos.x(), s.y() + s.height() - sp - m, EDGE_TILT_ANGLE
        ty = min(ty, s.y() + s.height() - sp - m)
        self.snapped_edge = e
        self.snap_target_x, self.snap_target_y = tx, ty
        self.snap_start_x, self.snap_start_y = pos.x(), pos.y()
        self.snap_target_rotation = rot
        self.snap_start_rotation = self._current_rotation
        self._snap_rotation = rot
        self.snap_anim_timer.start()
        self.anim_type = "snap"
        self._unsnap_active = False  # 吸附动画优先
        return True

    def mousePressEvent(self, event):
        self._touch_interact()
        # 点击桌宠：开始消失倒计时，并抑制悬停弹出直到鼠标离开
        self._hover_timer.stop()
        self._click_suppress = True
        self.status_bubble.start_hide_countdown()
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPos()
            self._did_drag = False
            self._is_pressed = True
            self._pending_unsnap_rot = None
            self.raise_()
            self.trigger_elastic_press()

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            d = event.globalPos() - self._press_pos
            if abs(d.x()) > 3 or abs(d.y()) > 3:
                self._did_drag = True
                self.is_dragging = True
                self._touch_interact()
                self._is_pressed = False
                self._hover_timer.stop()
                self.status_bubble.start_hide_countdown()
                self.drag_offset = event.pos()
                self._press_pos = None
                self.anim_type = None
                self.snapped_edge = None
                # 拖拽时保持当前旋转不立即归零，记住起始角度，等松手时再过渡
                if abs(self._current_rotation) > 0.5 or abs(self._snap_rotation) > 0.5:
                    self._pending_unsnap_rot = self._current_rotation
                self._snap_rotation = 0.0
        if self.is_dragging:
            np = self.mapToGlobal(event.pos() - self.drag_offset)
            self.move(np)
            if self.radial_menu.is_visible_state:
                self.radial_menu._reposition_geometry(self.radial_menu._margin, self.radial_menu._margin * 2)
                self.radial_menu._tip.hide()
                # 拖到屏幕边缘/角落时按新位置重排，平滑过渡方便观察排列
                if not self.radial_menu._animating:
                    self.radial_menu.reflow()

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                was = self._is_pressed and not self._did_drag
                self._is_pressed = False
                self._press_pos = None
                self._wobble_rot = 0.0
                self._wobble_x = 0.0
                self._wobble_y = 0.0
                if was:
                    self.radial_menu.toggle_menu(self.settings.get("slot_shortcuts", []))
                    # 点击松手：弹性缩放过冲恢复
                    self.trigger_elastic('open')
                if self.is_dragging:
                    self.is_dragging = False
                    self.snapped_edge = None
                    # 拖拽释放后若鼠标仍在桌宠图标上，0.1 秒后弹气泡
                    if self.underMouse():
                        self._hover_timer.start(100)
                    # 拖拽释放：弹性缩放过冲恢复
                    self.trigger_elastic('open')
                    # 是否处于边缘倾斜状态？检查 _pending_unsnap_rot / _current_rotation
                    if self._pending_unsnap_rot is not None and abs(self._pending_unsnap_rot) > 0.5:
                        s = QApplication.primaryScreen().availableGeometry()
                        in_edge_zone = False
                        pos = self.pos()
                        m = PET_SHADOW_MARGIN
                        w = self.pet_size
                        ld = pos.x() + m - s.x()
                        rd = s.x() + s.width() - (pos.x() + m + w)
                        td = pos.y() + m - s.y()
                        bd = s.y() + s.height() - (pos.y() + m + w)
                        if min(ld, rd, td, bd) <= EDGE_SNAP_THRESHOLD:
                            in_edge_zone = True
                        self._pending_unsnap_rot = None
                        if not in_edge_zone:
                            self._unsnap_active = True
                            self._unsnap_timer = QElapsedTimer()
                            self._unsnap_timer.start()
                            self._unsnap_start_rot = self._current_rotation
                            self._unsnap_start_pos = self.pos()
                            self._snap_rotation = 0.0
                            return
                    self._pending_unsnap_rot = None
                    # 尝试边缘吸附
                    if not self._check_edge_snap():
                        # 没有吸附：若仍有旋转，继续旋转归零动画
                        if abs(self._current_rotation) > 0.5:
                            self._unsnap_active = True
                            self._unsnap_timer = QElapsedTimer()
                            self._unsnap_timer.start()
                            self._unsnap_start_rot = self._current_rotation
                            self._unsnap_start_pos = self.pos()
                    else:
                        # 吸附成功取消回正动画
                        self._unsnap_active = False
        except Exception as e:
            import traceback
            traceback.print_exc()

    def contextMenuEvent(self, event):
        self.show_pet_menu(event.globalPos())

    def show_pet_menu(self, global_pos):
        """桌宠本体右键菜单（设置 / 气泡开关 / 气泡保持 / 退出）；径向菜单右键到桌宠时也复用。"""
        menu = StyledMenu()
        a_settings = menu.addAction("设置")
        a_bubble = menu.addAction("关闭气泡" if self.bubble_enabled else "打开气泡")
        a_keep = None
        if self.bubble_enabled:
            # 气泡保持：等同钉住，一直显示；已激活时显示"取消保持"
            b = getattr(self, "status_bubble", None)
            keeping = bool(getattr(b, "_pinned", False))
            a_keep = menu.addAction("取消气泡保持" if keeping else "气泡保持")
        menu.addSeparator()
        a_quit = menu.addAction("退出")
        chosen = menu.exec_(global_pos)
        if chosen == a_settings:
            _diag_log("context menu: chose settings")
            QTimer.singleShot(0, self._open_settings)
        elif chosen == a_bubble:
            self._toggle_bubble()
        elif a_keep is not None and chosen == a_keep:
            self._toggle_bubble_keep()
        elif chosen == a_quit:
            quit_handler = getattr(self, 'quit_requested', None)
            if callable(quit_handler):
                quit_handler()
            else:
                QApplication.quit()

    def _toggle_bubble_keep(self):
        """气泡保持：等同钉住——一直显示并跟随桌宠；点关闭按钮才消失。"""
        try:
            b = getattr(self, "status_bubble", None)
            if b is not None and hasattr(b, "_toggle_pin"):
                b._toggle_pin()
                if b._pinned:
                    # 保持 = 钉住：立即弹出气泡
                    b._do_show()
        except Exception:
            import traceback
            traceback.print_exc()

    def _toggle_bubble(self):
        """右键菜单：打开/关闭状态气泡（立即生效并保存）。"""
        try:
            self.bubble_enabled = not self.bubble_enabled
            self.settings["bubble_enabled"] = self.bubble_enabled
            save_settings(self.settings)
            if not self.bubble_enabled:
                self.status_bubble.hide_animated()
        except Exception:
            import traceback
            traceback.print_exc()

    def edit_rule_popup(self, rule):
        """气泡右键"编辑"：直接打开模块编辑窗口，保存后实时更新气泡。"""
        try:
            from pet_gravity import load_settings, save_settings
            old_dlg = getattr(self, '_settings_dlg', None)
            if old_dlg is not None and old_dlg.isVisible():
                old_dlg.raise_()
                old_dlg.activateWindow()
                return
            rid = (rule or {}).get("id")
            if not rid:
                return

            def _fin(result):
                try:
                    from pet_gravity import load_settings as _ls, save_settings as _ss
                    import status_monitor as _sm
                    _RULE_DIALOG_REF[0] = None
                    if result != QDialog.Accepted or not dlg.rule:
                        return
                    st = _ls()
                    rules = st.get("status_rules", [])
                    for i, r in enumerate(rules):
                        if r.get("id") == rid:
                            rules[i] = dlg.rule
                            break
                    else:
                        rules.append(dlg.rule)
                    st["status_rules"] = rules
                    _ss(st)
                    self.settings.update(st)
                    b = getattr(self, "status_bubble", None)
                    if b is not None:
                        b.set_config(rules=rules)
                except Exception:
                    import traceback
                    traceback.print_exc()

            dlg = RuleDialog(self, rule)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            _RULE_DIALOG_REF[0] = weakref.ref(dlg)
            dlg.finished.connect(_fin)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            import traceback
            traceback.print_exc()

    def _open_settings(self):
        _diag_log("open_settings called; old_dlg=%r" % (getattr(self, '_settings_dlg', None) is not None))
        # 设置窗口唯一：已打开时直接提到前台（防御陈旧/已销毁引用）
        old_dlg = getattr(self, '_settings_dlg', None)
        if old_dlg is not None:
            try:
                if old_dlg.isVisible():
                    _diag_log("  existing dialog raised to front")
                    old_dlg.raise_()
                    old_dlg.activateWindow()
                    return
                old_dlg.close()
            except Exception:
                pass
            self._settings_dlg = None
        try:
            try:
                self.status_bubble.hide_animated()
            except Exception:
                pass
            dlg = SettingsDialog(self.settings)
            _diag_log("  dialog created size=%dx%d" % (dlg.width(), dlg.height()))
            self._settings_dlg = dlg
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            # 居中到桌宠当前所在的屏幕，避免对话框打开在看不到的显示器上
            scr = QApplication.screenAt(self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2)))
            if scr is None:
                scr = QApplication.primaryScreen()
            s = scr.availableGeometry()
            dlg.move(s.x() + (s.width() - dlg.width()) // 2, s.y() + (s.height() - dlg.height()) // 2)
            # 非模态打开：设置窗口打开期间桌宠仍可点击/移动
            _ = dlg.winId()
            def _ensure_dlg_visible():
                try:
                    if sys.platform == 'win32':
                        import ctypes
                        # SW_SHOW：强制操作系统映射窗口，无论 Qt 内部状态如何
                        ctypes.windll.user32.ShowWindow(int(dlg.winId()), 5)
                    dlg.raise_()
                    dlg.activateWindow()
                except Exception:
                    pass
            QTimer.singleShot(0, _ensure_dlg_visible)
            # 诊断探针：+500ms 检查窗口是否真实存在
            def _probe_dlg():
                try:
                    _diag_log("  +500ms visible=%s winId=%s pos=(%d,%d) size=%dx%d" % (
                        dlg.isVisible(), int(dlg.winId()), dlg.x(), dlg.y(), dlg.width(), dlg.height()))
                except Exception as e:
                    _diag_log("  +500ms probe error: %r" % e)
            QTimer.singleShot(500, _probe_dlg)
            dlg.finished.connect(self._on_settings_finished)
            dlg.show()
            _diag_log("  after show visible=%s pos=(%d,%d)" % (dlg.isVisible(), dlg.x(), dlg.y()))
        except Exception:
            self._settings_dlg = None
            import traceback
            _diag_log("  EXCEPTION:\n%s" % traceback.format_exc())
            _error_log("open_settings EXCEPTION:\n%s" % traceback.format_exc())
            try:
                self.status_bubble.show_error_popup("设置打开失败", "请查看错误日志 %TEMP%\\oi_pet_error.log")
            except Exception:
                pass
            try:
                with open(os.path.join(os.environ.get("TEMP", os.environ.get("TMP", ".")),
                                       "oi_pet_settings_error.log"), "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc() + "\n")
            except Exception:
                pass
            traceback.print_exc()

    def _on_settings_finished(self, result):
        dlg = getattr(self, '_settings_dlg', None)
        self._settings_dlg = None
        _diag_log("  settings finished result=%s" % result)
        if dlg is None:
            return
        try:
            if result == QDialog.Accepted:
                self.settings = dlg.settings
                save_settings(self.settings)
                self._apply_pet_settings()
                if self.radial_menu.is_visible_state:
                    self.radial_menu.hide_menu(animate=False)
        except Exception:
            import traceback
            _diag_log("  EXCEPTION on finish:\n%s" % traceback.format_exc())
            _error_log("settings finished EXCEPTION:\n%s" % traceback.format_exc())
            try:
                self.status_bubble.show_error_popup("设置保存失败", "请查看错误日志 %TEMP%\\oi_pet_error.log")
            except Exception:
                pass
            traceback.print_exc()

    def _rebuild_status_bubble(self):
        """气泡档位变化后重建 UI；模型配置和用户设置保持不变。"""
        old = self.status_bubble
        was_visible = False
        pinned = False
        opacity = 0.95
        try:
            was_visible = bool(old.isVisible())
            pinned = bool(old._pinned)
            opacity = max(0.3, min(1.0, float(old._bubble_opacity)))
            old.prepare_for_rebuild()
        except Exception:
            import traceback
            traceback.print_exc()

        new = StatusBubbleLayout(self)
        self.status_bubble = new
        try:
            new._bubble_opacity = opacity
            new._op_slider.setValue(int(round(opacity * 100)))
            new.setWindowOpacity(opacity)
            new._pinned = pinned
            new._pin_btn.setText("●" if pinned else "○")
            new._pin_btn.setToolTip(
                "取消钉住" if pinned else "钉住气泡（一直显示并跟随桌宠）")
            new.update()
            new.set_config(self.settings.get("status_enabled"),
                           self.settings.get("status_custom_items"),
                           self.settings.get("status_rules"))
            self.bubble_enabled = self.settings.get("bubble_enabled", True)
            if was_visible and self.bubble_enabled:
                new.show_near()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if old is not None:
                try:
                    old.deleteLater()
                except RuntimeError:
                    pass

    def _apply_ui_scales(self):
        """应用两个独立档位；返回是否需要重建气泡。"""
        try:
            new_b = max(0.5, min(3.0, float(self.settings.get("bubble_scale", 1.0) or 1.0)))
            new_p = max(0.5, min(3.0, float(self.settings.get("pet_scale", 1.0) or 1.0)))
        except Exception:
            new_b, new_p = self._applied_bubble_scale, self._applied_pet_scale
        bubble_changed = abs(new_b - self._applied_bubble_scale) > 0.001
        pet_changed = abs(new_p - self._applied_pet_scale) > 0.001
        _kit.set_bubble_scale(new_b)
        _kit.set_pet_scale(new_p)
        self._applied_bubble_scale = new_b
        self._applied_pet_scale = new_p
        return bubble_changed, pet_changed

    def _apply_pet_settings(self):
        from widgets import kit as _kit
        bubble_changed, pet_scale_changed = self._apply_ui_scales()
        new_size = max(40, int(self.settings.get("pet_size",
                                  self.pet_config.get("size", 75))
                                  * _kit.pet_scale()))
        new_opacity = self.settings.get("pet_opacity", self.pet_opacity)
        new_image = self.settings.get("pet_image", "")
        menu_was_visible = False
        if (pet_scale_changed or new_size != self.pet_size) and self.radial_menu:
            menu_was_visible = self.radial_menu.is_visible_state
            try:
                self.radial_menu.hide_menu(animate=False)
            except Exception:
                pass
        size_changed = new_size != self.pet_size
        image_changed = new_image != self._last_pet_image
        if size_changed:
            self.pet_size = new_size
            self.setFixedSize(self.pet_size + PET_SHADOW_MARGIN * 2,
                              self.pet_size + PET_SHADOW_MARGIN * 2)
        if size_changed or image_changed:
            self._last_pet_image = new_image
            self._load_image()
        if pet_scale_changed:
            try:
                self._load_image()
            except Exception:
                pass
        self.pet_opacity = new_opacity
        self.setWindowOpacity(self.pet_opacity)
        if bubble_changed:
            self._rebuild_status_bubble()
        else:
            self.status_bubble.set_config(self.settings.get("status_enabled"),
                                          self.settings.get("status_custom_items"),
                                          self.settings.get("status_rules"))
        self._mood_enabled = self.settings.get("mood_enabled", True)
        self.bubble_enabled = self.settings.get("bubble_enabled", True)
        if menu_was_visible and self.radial_menu:
            shortcuts = list(self.settings.get("slot_shortcuts", []))
            QTimer.singleShot(0, lambda: self.radial_menu.show_menu(shortcuts))

    def hide(self):
        """隐藏桌宠时一并隐藏气泡（钉住状态保留）"""
        try:
            self.status_bubble.hide()
        except Exception:
            pass
        super().hide()

    def closeEvent(self, event):
        self.frame_timer.stop()
        self.fs_timer.stop()
        if getattr(self, '_mood_timer', None) is not None:
            self._mood_timer.stop()
        if getattr(self, '_hover_timer', None) is not None:
            self._hover_timer.stop()
        if self._gif_movie is not None:
            self._gif_movie.stop()
        if self._webp_anim is not None:
            self._webp_anim.stop()
        if self.radial_menu:
            self.radial_menu.hide()
        if self.status_bubble:
            self.status_bubble.hide()
        event.accept()
