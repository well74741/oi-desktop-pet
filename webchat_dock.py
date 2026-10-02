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

代价（已知、刻意接受）：Edge 不给第三方留内容区，我们**没法把页面挤窄**，
这条栏只能盖在页面左侧。所以收起态做成了"左缘正中间一个小把手"（`EDGE_W` 宽、
`HANDLE_H` 高，实测只遮住窗口面积的 0.4%）再加一点半透明，鼠标移上去才**横向**滑成整条栏
——用户反馈"页面左侧显示不完整，会被收纳条遮挡住一部分"就是这么来的。

两件踩过的坑，这里都有代码兜（都由 `_check_dock_life.py` 量过）：
- **父窗口销毁会连带销毁子窗口**。用户把网页窗口一关，我们的原生句柄就被浏览器
  带走了，之后再挂永远失败（实测 `alive=False parent=None`）——这就是"再次打开
  没有侧边栏"。所以有 `is_dead()`，由 `webchat_ui.dock()` 发现尸体就换新的。
- **收起态不能靠"把全宽的栏硬裁一条"**。那样露出来的是半个按钮，又丑又读不懂
  （用户反馈"强行裁切了一条，显示也不完整"）。现在收起时栏是**整条滑出去**并
  隐藏，窄边上画一个明确的把手。
- **半透明只能走 Qt 的 `setWindowOpacity`**。自己 `SetWindowLongW` 置
  `WS_EX_LAYERED` 当场读回来是生效的，但 Qt 之后碰一下这个窗口就按它自己那套
  flags 重算并覆盖整个 `GWL_EXSTYLE`，layered 位被抹掉 —— 几何全对、`exstyle`
  却一直是 0。跟 Qt 抢这个字段抢不赢。
"""
import ctypes

from PyQt5.QtCore import QEvent, QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
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
    EDGE_W = 14            # 收起态留的那条窄边（逻辑像素）
    HANDLE_H = 64          # 收起态只占这么高（居中），不再贴满整条左缘
    COLLAPSED_ALPHA = 236  # 收起态不透明度（255 的 92.5%，= 7.5% 透明）
    SLIDE_MS = 16          # 滑出/滑回的动画步长
    SLIDE_DUR = 0.18       # 滑出/滑回的时长（秒），和桌宠其他折叠动画一个手感

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
        self._handle_h = kit.ui(self.HANDLE_H)
        self._k = 0.0              # 0 = 收起（小把手），1 = 展开（整条栏）
        self._w = self._edge_w     # 由 _k 推出来的当前宽度（探针/测试在看）
        self.resize(self._edge_w, self._handle_h)
        self._own_hwnd = int(self.winId())   # 记下来：句柄被系统销毁后还要查
        self._timer = QTimer(self)
        self._timer.setInterval(self.SYNC_MS)
        self._timer.timeout.connect(self._sync)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(450)
        self._collapse_timer.timeout.connect(lambda: self._set_expanded(False))
        self._slide = QTimer(self)
        self._slide.setInterval(self.SLIDE_MS)
        self._slide.timeout.connect(self._slide_tick)
        self._slide_from = self._slide_to = 0.0
        self._slide_t0 = 0.0
        self.bar.hide()            # 初始是收起态，只有那个小把手

    # ---------- 生死 ----------
    def parent_hwnd(self):
        """当前挂在哪个浏览器窗口上（切站点时要拿它的矩形）。"""
        return self._hwnd

    def is_dead(self):
        """我们自己的原生窗口是不是已经被系统销毁了。

        父窗口（浏览器窗口）销毁时会连带销毁子窗口，Qt 这边毫不知情，
        之后所有 `SetParent` / 摆位都是对一个废句柄操作，静默失败。
        """
        try:
            return not bool(_u.IsWindow(ctypes.c_void_p(self._own_hwnd)))
        except Exception:
            return False

    def forget_native(self):
        """句柄已经没了，别让 Qt 的析构再去 DestroyWindow 它。"""
        self._timer.stop()
        self._collapse_timer.stop()
        self._slide.stop()
        self._hwnd = None
        try:
            self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
            self.createWinId = lambda: None      # noqa: 不再去碰原生层
        except Exception:
            pass

    # ---------- 挂接 ----------
    def attach_to(self, hwnd):
        """挂进浏览器窗口，成为它的子窗口。"""
        if not hwnd:
            return False
        self._hwnd = int(hwnd)
        self.show()                      # 先有原生窗口，才能改样式
        self._own_hwnd = int(self.winId())   # show() 可能重建过原生窗口
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
        """解开挂接、停表、藏起来（浏览器关了或切回旧架构时用）。

        **必须把样式还原成顶层窗口**（清 `WS_CHILD`、补回 `WS_POPUP`），
        这是 `_reparent` 的严格逆操作。只调 `SetParent(None)` 的话会留下一个
        "WS_CHILD 但没有父窗口"的怪状态，之后再 `attach_to` 必定失败 ——
        实测就是这样：关掉网页窗口后贴边栏句柄还活着，但 `parent=None` 且
        永远挂不回去，表现和"被连带销毁"一模一样（都是没有侧边栏）。
        """
        self._timer.stop()
        self._collapse_timer.stop()
        self._slide.stop()
        try:
            if not self.is_dead():
                h = int(self.winId())
                st = _u.GetWindowLongW(ctypes.c_void_p(h),
                                       _GWL_STYLE) & 0xFFFFFFFF
                _u.SetWindowLongW(ctypes.c_void_p(h), _GWL_STYLE,
                                  _as_long((st & ~_WS_CHILD) | _WS_POPUP))
                _u.SetParent(ctypes.c_void_p(h), None)
        except Exception:
            pass
        self._hwnd = None
        try:
            self.hide()
        except Exception:
            pass

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
        if self.is_dead():
            # 浏览器窗口被关掉，把我们这个子窗口一起带走了。别再对废句柄动手，
            # 停表就好；下一次 webchat_ui.dock() 会发现尸体并换一个新的。
            self.forget_native()
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
        """用 Win32 摆自己：Qt 的 setGeometry 会被它自己的布局改回去。

        收起态**只占中间一小段高度**（`HANDLE_H`），不再贴满整条左缘 ——
        用户反馈"页面左侧显示不完整，会被收纳条遮挡住一部分"，而 Edge 不给
        第三方留内容区，我们没法把页面挤窄，只能让自己少占地方。

        **展开是纯横向的**：只要开始展开（`_k > 0`），高度立刻就是满高、顶边对齐，
        之后只有宽度在动。早先版本把高度和纵向位置也一起插值，看起来是"从左缘
        正中间往上下撑开"，用户说"侧边栏不要从中间展开吧，从左侧边缘横向内展开
        就行"。那一下高度切换发生在宽度还只有 `EDGE_W`（21px）、而且半透明的时候，
        基本看不见。
        """
        box = self._client_box()
        if box is None:
            return
        top, full_h = box
        k = max(0.0, min(1.0, self._k))
        w = self._edge_w + (self._full_w - self._edge_w) * k
        w = max(self._edge_w, int(round(w)))
        self._w = w
        if k <= 0.0:
            h = self._handle_h                  # 收起到位：左缘中间那个小把手
            y = top + int(round((max(full_h, h) - h) / 2.0))
        else:
            h = max(full_h, self._handle_h)     # 一开始展开就是满高，只有宽在动
            y = top
        h = max(1, int(h))
        try:
            _u.SetWindowPos(ctypes.c_void_p(int(self.winId())),
                            ctypes.c_void_p(_HWND_TOP),
                            0, y, w, h, _SWP_NOACTIVATE)
        except Exception:
            pass
        # 整条栏**滑进滑出**：左缘停在 w - full_w，展开时正好是 0。
        # 以前是把全宽的栏钉在 x=0 让窗口去裁，收起时露出来的是半个按钮
        # ——用户说的"强行裁切了一条，显示也不完整"。
        self.bar.setGeometry(w - self._full_w, 0, self._full_w, h)
        show_bar = w > self._edge_w + kit.ui(2)
        if show_bar != self.bar.isVisible():
            self.bar.setVisible(show_bar)
        self._apply_alpha(k)
        self.update()

    def _apply_alpha(self, k):
        """收起态半透明（用户要的 ~15% 透明），展开态实心。

        **必须走 Qt 的 `setWindowOpacity`，不能自己去置 `WS_EX_LAYERED`。**
        实测：自己 `SetWindowLongW(GWL_EXSTYLE, |WS_EX_LAYERED)` 当场是生效的
        （读回来 0x00080000），但只要 Qt 之后碰一下这个窗口（显示/改尺寸/重绘），
        它就按自己那套 flags **重算并覆盖整个 GWL_EXSTYLE**，layered 位被抹掉，
        于是几何全对、`exstyle` 却一直是 0。跟 Qt 抢这个字段是抢不赢的。
        """
        o = (self.COLLAPSED_ALPHA / 255.0
             + (1.0 - self.COLLAPSED_ALPHA / 255.0) * max(0.0, min(1.0, k)))
        o = round(o, 3)
        if o == getattr(self, "_alpha_now", None):
            return
        try:
            self.setWindowOpacity(o)
            self._alpha_now = o
        except Exception:
            pass

    # ---------- 收起态的把手 ----------
    def paintEvent(self, ev):
        """收起态画一个小把手；展开态画栏的底色。

        收起态是**常态**，所以它必须看着是刻意设计的一个把手，而不是一条
        被切掉一半的侧边栏，而且要尽量少挡页面。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if self.bar.isVisible():
            p.fillRect(0, 0, w, h, QColor(21, 27, 38))
            p.setPen(QPen(QColor(42, 51, 70), 1))
            p.drawLine(w - 1, 0, w - 1, h)
            return
        # 收起态：整个窗口就是那个把手，右侧圆角，左边贴着窗口边缘
        r = kit.ui(5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 35, 49))
        p.drawRoundedRect(QRectF(-r, 0, w + r, h), r, r)
        gw, gh = max(2, kit.ui(3)), min(kit.ui(26), max(kit.ui(8), h - kit.ui(16)))
        p.setBrush(QColor(74, 158, 255, 210))
        p.drawRoundedRect(QRectF((w - gw) / 2.0, (h - gh) / 2.0, gw, gh),
                          gw / 2.0, gw / 2.0)

    # ---------- 自动隐藏 ----------
    def _set_expanded(self, on):
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        self._slide_to = 1.0 if on else 0.0
        self._slide_from = self._k
        if abs(self._slide_to - self._slide_from) < 0.01:
            self._k = self._slide_to
            self._apply_geom()
            return
        if on:
            self.bar.setVisible(True)    # 滑出来之前先让它存在，否则第一帧是空的
        import time
        self._slide_t0 = time.monotonic()
        if not self._slide.isActive():
            self._slide.start()

    def _slide_tick(self):
        """滑动一帧。缓动用和桌宠其他折叠动画同一条曲线（smoothstep）。"""
        import time
        from bubble_ui import ease_in_out
        if self.is_dead():
            self._slide.stop()
            self.forget_native()
            return
        t = (time.monotonic() - self._slide_t0) / max(0.01, self.SLIDE_DUR)
        done = t >= 1.0
        e = 1.0 if done else ease_in_out(t)
        self._k = self._slide_from + (self._slide_to - self._slide_from) * e
        self._apply_geom()
        if done:
            self._slide.stop()
            self._k = self._slide_to
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
