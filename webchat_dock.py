# -*- coding: utf-8 -*-
"""聚合AI 的「贴边栏」：把站点栏直接挂进浏览器窗口里，当它的子窗口。

为什么要有这个（和 webchat_ui.WebChatHost 的区别）：
------------------------------------------------------------------
原来的做法是我们开一个宿主窗口（侧边栏 + 容器），再把浏览器的 `--app` 窗口
摆到容器上。两个顶层窗口、分属两个进程，**永远不可能原子地一起移动**：桌宠先
移动自己，再让浏览器跟上，中间那一帧就是用户看到的"断层 / 拖动延迟"。

这里反过来：浏览器窗口就是那个窗口，站点栏置 `WS_CHILD` 之后 `SetParent` 进去，
变成它的子窗口。**位置由系统保证跟随**——实测连续移动父窗口，子窗口相对偏移
恒定，我们一行摆位代码都不用写，结构上不可能错位。

实测确认过的（_probe_rp2.py）：
- `SetParent` 前必须先把样式改成 `WS_CHILD`（只调 SetParent 的话只是 owned，
  `GetParent` 仍然是 0）；
- 挂进去之后我们的窗口是**第一个子窗口**，z 序在 `Chrome_RenderWidgetHostHWND`
  之上，不会被页面盖住；
- 跨进程子窗口**能正常收鼠标**；
- 断开之后可以重新挂接（Edge 重建窗口时要用）。

实测确认**不成立**的，所以下面有代码兜：
- **尺寸不跟随**。父窗口客户区高度变了，子窗口高度不会变，得我们自己改。
  所以有一个低频定时器盯着父窗口矩形（只在变化时才动手）。
- Qt 会重新主张自己的宽度（要 48 它给 136），所以几何必须在挂接之后再设，
  而且用 Win32 的 `SetWindowPos`，不要用 Qt 的 setGeometry。

代价（已知、刻意接受）：Edge 不给第三方留内容区，所以这条栏是**盖在**页面左侧的。
因此默认收成一条窄边，鼠标移上去才滑出——只在用的时候占地方。
"""
import ctypes

from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtWidgets import QWidget

from widgets import kit

_u = ctypes.windll.user32
for _fn in ("SetParent", "GetParent", "GetWindow"):
    getattr(_u, _fn).restype = ctypes.c_void_p
_u.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_u.GetParent.argtypes = [ctypes.c_void_p]

_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_SWP_NOACTIVATE = 0x0010
_HWND_TOP = 0


def _as_long(v):
    """Win32 的 LONG 是有符号的；样式位带最高位时要转成负数再传。"""
    return ctypes.c_long(v - 0x100000000 if v > 0x7FFFFFFF else v)


def _rect(hwnd):
    """窗口矩形 (x, y, w, h)；拿不到返回 None。"""
    try:
        import struct
        buf = ctypes.create_string_buffer(16)
        if not _u.GetWindowRect(ctypes.c_void_p(hwnd), buf):
            return None
        l, t, r, b = struct.unpack("4i", buf.raw)
        return (l, t, r - l, b - t)
    except Exception:
        return None


class DockBar(QWidget):
    """站点栏的外壳：负责挂进浏览器窗口、跟住它的尺寸、以及自动隐藏。

    站点按钮本身复用 `webchat_ui.WebChatSidebar`，这里不重复做一套。
    """

    SYNC_MS = 120          # 盯父窗口尺寸的间隔（只在变化时才动手，代价极低）
    EDGE_W = 10            # 收起态留的那条窄边（逻辑像素）

    def __init__(self):
        super().__init__(None)
        from webchat_ui import WebChatSidebar
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setProperty("oi_nozoom", True)
        self.setStyleSheet("background:#151b26;")
        self.bar = WebChatSidebar(self)
        self.bar.move(0, 0)
        self._hwnd = None          # 挂在哪个浏览器窗口上
        self._parent_rect = None   # 上一次看到的父窗口矩形
        self._expanded = False
        self._full_w = kit.ui(WebChatSidebar.PANEL_W)
        self._edge_w = kit.ui(self.EDGE_W)
        self.resize(self._edge_w, kit.ui(200))
        self._timer = QTimer(self)
        self._timer.setInterval(self.SYNC_MS)
        self._timer.timeout.connect(self._sync)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(450)
        self._collapse_timer.timeout.connect(lambda: self._set_expanded(False))

    # ---------- 挂接 ----------
    def attach_to(self, hwnd):
        """挂进浏览器窗口，成为它的子窗口。"""
        if not hwnd:
            return False
        self._hwnd = int(hwnd)
        self.show()                      # 先有原生窗口，才能改样式
        ok = self._reparent()
        if ok:
            self._parent_rect = None     # 逼 _sync 立刻摆一次
            self._sync()
            if not self._timer.isActive():
                self._timer.start()
        return ok

    def _reparent(self):
        """置 WS_CHILD 再 SetParent —— 顺序不能换（见模块注释）。"""
        try:
            h = int(self.winId())
            st = _u.GetWindowLongW(ctypes.c_void_p(h), _GWL_STYLE) & 0xFFFFFFFF
            _u.SetWindowLongW(ctypes.c_void_p(h), _GWL_STYLE,
                              _as_long((st & ~_WS_POPUP) | _WS_CHILD))
            _u.SetParent(ctypes.c_void_p(h), ctypes.c_void_p(self._hwnd))
            return _u.GetParent(ctypes.c_void_p(h)) == self._hwnd
        except Exception:
            return False

    def detach(self):
        """解开挂接、停表、藏起来（浏览器关了或切回旧架构时用）。"""
        self._timer.stop()
        self._collapse_timer.stop()
        try:
            if self.winId():
                _u.SetParent(ctypes.c_void_p(int(self.winId())), None)
        except Exception:
            pass
        self._hwnd = None
        self.hide()

    # ---------- 几何同步 ----------
    def _client_box(self):
        """父窗口客户区在**父窗口坐标系**里的 (top, height)。

        Chromium 的 `--app` 窗口把标题栏画在客户区里，所以这里不去减标题栏：
        子窗口坐标就是父窗口矩形的左上角起算，整条贴满高度即可。
        """
        pr = _rect(self._hwnd)
        if not pr:
            return None
        return (0, pr[3])

    def _sync(self):
        """父窗口尺寸变了就跟一下。位置不用管——系统已经替我们跟了。"""
        if not self._hwnd:
            return
        pr = _rect(self._hwnd)
        if not pr:
            self.detach()
            return
        # 挂接掉了（Edge 重建过窗口）就重新挂
        try:
            if _u.GetParent(ctypes.c_void_p(int(self.winId()))) != self._hwnd:
                if not self._reparent():
                    return
                self._parent_rect = None
        except Exception:
            return
        if pr == self._parent_rect:
            return                        # 没变化，什么都不做
        self._parent_rect = pr
        self._apply_geom()

    def _apply_geom(self):
        """用 Win32 摆自己：Qt 的 setGeometry 会被它自己的布局改回去。"""
        box = self._client_box()
        if box is None:
            return
        top, h = box
        w = self._full_w if self._expanded else self._edge_w
        try:
            _u.SetWindowPos(ctypes.c_void_p(int(self.winId())),
                            ctypes.c_void_p(_HWND_TOP),
                            0, top, w, h, _SWP_NOACTIVATE)
        except Exception:
            pass
        # 里面那条站点栏始终是全宽：收起时被自己的窗口裁掉，露出来的就是窄边
        self.bar.setGeometry(0, 0, self._full_w, h)

    # ---------- 自动隐藏 ----------
    def _set_expanded(self, on):
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        self._apply_geom()

    def enterEvent(self, ev):
        super().enterEvent(ev)
        self._collapse_timer.stop()
        self._set_expanded(True)

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        self._collapse_timer.start()      # 晚一点再收，手抖一下不会闪

    def event(self, ev):
        # 子控件上的进入/离开也算在栏上（鼠标在按钮上时不要收起）
        if ev.type() == QEvent.Enter:
            self._collapse_timer.stop()
            self._set_expanded(True)
        return super().event(ev)
