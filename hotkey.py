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

from PyQt5.QtCore import QObject, pyqtSignal

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
