# -*- coding: utf-8 -*-
"""全局热键：不管焦点在哪个程序，按下组合键就在鼠标位置唤出径向菜单。

为什么用 `RegisterHotKey` 而不是键盘钩子：
`RegisterHotKey` 是系统提供的正式接口，**空闲时不耗任何资源**，也不需要管理员
权限、不会被安全软件当成键盘记录器。代价是它不区分左右修饰键，而且组合键可能
被别的软件抢走 —— 抢不到时我们如实告诉用户，不装作成功。

只在 Windows 上工作；其他平台 `supported()` 返回 False，调用方据此禁用设置项。
"""
import ctypes
import ctypes.wintypes as wt
import re
import sys

import threading

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLineEdit, QToolTip

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
_HOTKEY_ID = 0xA1
_WM_APP_QUIT = 0x8000 + 17     # WM_APP+17：用来唤醒热键线程退出

# 命名键 → 虚拟键码（只收常见的长尾，够用且不必引入依赖）
_NAMED = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(1, 25):
    _NAMED["f%d" % _i] = 0x6F + _i          # F1=0x70
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN}
_ORDER = ("ctrl", "alt", "shift", "win")


def supported():
    return sys.platform == "win32"


def parse(spec):
    """把 "Ctrl+Alt+Space" 解析成 (modifiers, vk)；不合法返回 (0, 0)。

    要求至少一个修饰键 + 一个主键：单键（比如一个 F12）也能注册，但**故意不支持**
    —— 那样太容易和正在用的程序打架，用户会以为是桌宠出了故障。
    """
    s = str(spec or "").strip()
    if not s:
        return (0, 0)
    parts = [p.strip().lower() for p in re.split(r"[+\s]+", s) if p.strip()]
    if len(parts) < 2:
        return (0, 0)
    mods, keys = 0, []
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        else:
            keys.append(p)
    if not mods or len(keys) != 1:
        return (0, 0)
    k = keys[0]
    if k in _NAMED:
        vk = _NAMED[k]
    elif re.fullmatch(r"[a-z]", k):
        vk = ord(k.upper())
    elif re.fullmatch(r"[0-9]", k):
        vk = ord(k)
    else:
        return (0, 0)
    return (mods, vk)


def normalize(spec):
    """规范化成显示用写法：Ctrl+Alt+Space。解析不了则原样返回。"""
    mods, vk = parse(spec)
    if not vk:
        return str(spec or "").strip()
    names = []
    for m in _ORDER:
        if mods & _MODS[m]:
            names.append({"ctrl": "Ctrl", "alt": "Alt",
                          "shift": "Shift", "win": "Win"}[m])
    for k, v in _NAMED.items():
        if v == vk and k not in ("return", "escape"):
            names.append(k.capitalize())
            break
    else:
        if 0x30 <= vk <= 0x5A or 0x60 <= vk <= 0x69:
            names.append(chr(vk))
    return "+".join(names) if len(names) == _count_mods(mods) + 1 else str(spec)


def _count_mods(mods):
    return sum(1 for m in ("ctrl", "alt", "shift", "win") if mods & _MODS[m])


class GlobalHotkey(QObject):
    """注册一个全局热键；按下时发 `pressed`。

    `register()` 返回 (成功?, 提示文字)。**失败要如实说**：组合键被别的程序
    占用时 `RegisterHotKey` 返回 0，这时要告诉用户换一个，而不是静默失败。

    **为什么自己开线程跑消息循环**：`RegisterHotKey(NULL, ...)` 把热键挂到
    **调用线程的消息队列**上，按下时发的是一条**线程消息**（不是投给某个窗口的）。
    实测 Qt 的 `nativeEventFilter` 收不到它 —— 那个过滤器只看得到窗口消息
    （当时消息循环里 1/3/5/… 这些都在，唯独没有 WM_HOTKEY）。所以自己起一个
    线程专职 `GetMessageW`，收到就发信号（跨线程信号是队列投递，安全）。
    代价：一个空闲就阻塞在 GetMessageW 上的线程，不占 CPU。
    """

    pressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id = 0
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()

    def register(self, spec):
        if not supported():
            return (False, "仅 Windows 支持全局热键")
        self.unregister()
        mods, vk = parse(spec)
        if not vk:
            return (False, "热键格式不对。写法如 Ctrl+Alt+Space，需要至少一个修饰键")
        u = ctypes.windll.user32
        u.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_uint, ctypes.c_uint]
        u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_uint]
        result = {}
        self._ready = threading.Event()

        def worker():
            result["ok"] = bool(u.RegisterHotKey(
                None, _HOTKEY_ID, mods | MOD_NOREPEAT, vk))
            result["err"] = ctypes.GetLastError()
            # 记下线程 id，注销时用它把消息循环唤醒
            result["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
            self._ready.set()
            if not result["ok"]:
                return
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and int(msg.wParam) == _HOTKEY_ID:
                    try:
                        self.pressed.emit()      # 跨线程发信号 = 队列投递，安全
                    except Exception:
                        pass
                if msg.message == _WM_APP_QUIT:
                    break
            u.UnregisterHotKey(None, _HOTKEY_ID)

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="oi-hotkey")
        self._thread.start()
        if not self._ready.wait(3.0):
            return (False, "注册热键超时，请重试")
        if not result.get("ok"):
            err = result.get("err") or 0
            self._thread = None
            if err == 1409:      # ERROR_HOTKEY_ALREADY_REGISTERED
                return (False, "%s 已被其他程序占用，换一个吧" % normalize(spec))
            return (False, "注册失败（错误码 %s），换一个组合键试试" % err)
        self._id = _HOTKEY_ID
        self._tid = result.get("tid") or 0
        return (True, "")

    def unregister(self):
        if self._tid:
            try:
                # 往那个线程投一条自定义消息，把阻塞在 GetMessageW 的它叫醒
                ctypes.windll.user32.PostThreadMessageW(
                    self._tid, _WM_APP_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._tid = 0
        self._id = 0

    def registered(self):
        return bool(self._id)



# ---------- 录入框：点一下，按下组合键就录进去 ----------
_QT_NAMED = {
    Qt.Key_Space: "Space", Qt.Key_Tab: "Tab", Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter", Qt.Key_Escape: "Esc", Qt.Key_Insert: "Insert",
    Qt.Key_Delete: "Delete", Qt.Key_Home: "Home", Qt.Key_End: "End",
    Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown", Qt.Key_Up: "Up",
    Qt.Key_Down: "Down", Qt.Key_Left: "Left", Qt.Key_Right: "Right",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_QuoteLeft: "`", Qt.Key_Minus: "-", Qt.Key_Equal: "=",
    Qt.Key_BracketLeft: "[", Qt.Key_BracketRight: "]", Qt.Key_Backslash: "\\",
    Qt.Key_Semicolon: ";", Qt.Key_Apostrophe: "'", Qt.Key_Comma: ",",
    Qt.Key_Period: ".", Qt.Key_Slash: "/",
}
_QT_MODS = ((Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"),
            (Qt.ShiftModifier, "Shift"), (Qt.MetaModifier, "Win"))
_MOD_KEYS = (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
             Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R)
# 修饰键本身被按下 / 松开时，Qt 报告的 modifiers() 里**可能还没算上 / 还没去掉**它
# （Qt 文档：与平台有关）。所以按下时补上、松开时扣掉这个键自己的标志。
_KEY_FLAG = {Qt.Key_Control: Qt.ControlModifier, Qt.Key_Alt: Qt.AltModifier,
             Qt.Key_AltGr: Qt.AltModifier, Qt.Key_Shift: Qt.ShiftModifier,
             Qt.Key_Meta: Qt.MetaModifier, Qt.Key_Super_L: Qt.MetaModifier,
             Qt.Key_Super_R: Qt.MetaModifier}
_ANY_MOD = Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier


def key_name(qt_key):
    """Qt 键码 → 我们的键名（parse() 认得的写法）；认不出返回 ""。"""
    if Qt.Key_A <= qt_key <= Qt.Key_Z or Qt.Key_0 <= qt_key <= Qt.Key_9:
        return chr(qt_key)
    if Qt.Key_F1 <= qt_key <= Qt.Key_F24:
        return "F%d" % (qt_key - Qt.Key_F1 + 1)
    return _QT_NAMED.get(qt_key, "")


def combo_text(modifiers, qt_key):
    """(Qt 修饰键, Qt 键码) → "Ctrl+Alt+Space"；主键认不出返回 ""。"""
    k = key_name(qt_key)
    if not k:
        return ""
    mods = [name for flag, name in _QT_MODS if modifiers & flag]
    return "+".join(mods + [k])


# ---------- 录入时的低级键盘钩子 ----------
# 为什么需要：别的程序（输入法、启动器、截图工具……）已经 RegisterHotKey 占了某个组合
# 时，系统会在按键送到任何窗口**之前**把它截走 —— 录入框只收得到 Ctrl、Alt，主键
# 永远到不了。用户实测就是"Ctrl+Alt+Space 前两个键能识别到，空格识别不到"。
# WH_KEYBOARD_LL 在系统热键处理之前就能看到按键，所以只在录入框有焦点时挂上，
# 离开焦点立刻摘掉（不常驻，不监听平时的键盘）。
_WH_KEYBOARD_LL = 13
_WM_KEYDOWN, _WM_SYSKEYDOWN = 0x0100, 0x0104
_VK_MODS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}
_VK_NAME = {v: k for k, v in _NAMED.items() if k not in ("return", "escape")}


def _vk_name(vk):
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    n = _VK_NAME.get(vk, "")
    return n.upper() if n.startswith("f") and n[1:].isdigit() else n.capitalize() if n.isalpha() else n


class _KBDLL(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


if supported():
    _HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    _u32 = ctypes.windll.user32
    _u32.SetWindowsHookExW.restype = ctypes.c_void_p
    _u32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wt.DWORD]
    _u32.CallNextHookEx.restype = ctypes.c_ssize_t
    _u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    _u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _u32.GetAsyncKeyState.restype = ctypes.c_short
    ctypes.windll.kernel32.GetModuleHandleW.restype = ctypes.c_void_p


def _held(vk):
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyEdit(QLineEdit):
    """快捷键录入框：点进去，**直接按下**组合键就录好，不用一个字一个字打。

    - 只按了修饰键时先显示 "Ctrl+Alt+…"，松开没按主键就恢复原来的；
    - 不带修饰键直接按 Backspace / Delete = 清空（不用热键）；
    - 不带修饰键按别的键：提示"至少带一个 Ctrl / Alt / Shift / Win"，不录；
    - 拦下 ShortcutOverride：不然 Alt+字母 这类组合会先被对话框的快捷键吃掉。
    录入期间回调 on_capture(True/False)，调用方据此**暂停桌宠自己的全局热键** ——
    否则重新录入正在用的那个组合时，按下去就被全局热键截走、直接弹出菜单了。
    """

    captured = pyqtSignal(str)      # 录到一个组合（调用方据此当场检查是否被占用）

    def __init__(self, parent=None, on_capture=None):
        super().__init__(parent)
        self._on_capture = on_capture
        self._before = ""
        self.setPlaceholderText("点这里，按下组合键")
        self.setClearButtonEnabled(True)
        # 中文输入法开着时空格会被拿去选字/上屏，录入框里没有输入法的事
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self._hook = None
        self._hook_proc = None      # 必须持有引用，否则回调被回收 → 进程崩溃
        self.setToolTip("点一下，然后直接按下想用的组合键（如 Ctrl+Alt+Space）。\n"
                        "至少要带一个 Ctrl / Alt / Shift / Win。按 Backspace 清空 = 不用热键。")

    def event(self, ev):
        if ev.type() == QEvent.ShortcutOverride:
            ev.accept()
            return True
        return super().event(ev)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self._before = self.text()
        if callable(self._on_capture):
            self._on_capture(True)
        self._install_hook()

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self._remove_hook()
        if self.text().endswith("…"):
            self.setText(self._before)
        if callable(self._on_capture):
            self._on_capture(False)

    def hideEvent(self, ev):
        self._remove_hook()            # 窗口直接关掉时 focusOut 不一定来
        super().hideEvent(ev)

    def _install_hook(self):
        if not supported() or self._hook:
            return
        def proc(code, wparam, lparam):
            try:
                if code == 0 and wparam in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                    vk = ctypes.cast(lparam, ctypes.POINTER(_KBDLL)).contents.vkCode
                    if vk not in _VK_MODS:
                        mods = [n for vks, n in (((0x11,), "Ctrl"), ((0x12,), "Alt"),
                                                 ((0x10,), "Shift"), ((0x5B, 0x5C), "Win"))
                                if any(_held(v) for v in vks)]
                        name = _vk_name(vk)
                        if mods and name:
                            # 有修饰键 + 认得的主键：录下来，并**吞掉**这次按键 ——
                            # 不吞的话占着这个组合的那个程序会被触发
                            combo = normalize("+".join(mods + [name]))
                            QTimer.singleShot(0, lambda c=combo: self._captured(c))
                            return 1
            except Exception:
                pass
            return _u32.CallNextHookEx(None, code, wparam, lparam)
        self._hook_proc = _HOOKPROC(proc)
        self._hook = _u32.SetWindowsHookExW(
            _WH_KEYBOARD_LL, self._hook_proc,
            ctypes.windll.kernel32.GetModuleHandleW(None), 0) or None

    def _remove_hook(self):
        if self._hook:
            try:
                _u32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
        self._hook = None

    def _captured(self, combo):
        if not self.hasFocus():
            return
        self.setText(combo)
        self._before = combo
        self.captured.emit(combo)

    def keyPressEvent(self, ev):
        k = ev.key()
        mods = ev.modifiers()
        if k in _MOD_KEYS:
            mods = mods | _KEY_FLAG.get(k, Qt.NoModifier)
            names = [name for flag, name in _QT_MODS if mods & flag]
            self.setText("+".join(names) + "+…" if names else self._before)
            return
        plain = not (mods & (Qt.ControlModifier | Qt.AltModifier |
                             Qt.ShiftModifier | Qt.MetaModifier))
        if plain and k in (Qt.Key_Backspace, Qt.Key_Delete):
            self.clear()
            self._before = ""
            return
        if plain and k == Qt.Key_Tab:
            super().keyPressEvent(ev)       # 让 Tab 照常切到下一个控件
            return
        txt = combo_text(mods, k)
        if not txt:
            return                          # 认不出的键：什么都不做
        if plain:
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()),
                              "至少要带一个 Ctrl / Alt / Shift / Win", self)
            self.setText(self._before)
            return
        self.setText(normalize(txt))
        self._before = self.text()
        self.captured.emit(self._before)

    def keyReleaseEvent(self, ev):
        if self.text().endswith("…"):
            left = ev.modifiers() & ~_KEY_FLAG.get(ev.key(), Qt.NoModifier) & _ANY_MOD
            names = [name for flag, name in _QT_MODS if left & flag]
            # 还有修饰键按着就更新预览；全松开了还没按主键 → 恢复原来那个
            self.setText("+".join(names) + "+…" if names else self._before)
        super().keyReleaseEvent(ev)
