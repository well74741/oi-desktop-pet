# -*- coding: utf-8 -*-
"""已经开着的程序：找到它的主窗口并切到前面，而不是再启动一个。

用在快捷启动上（径向菜单 / 气泡里的启动器）：点微信、IDE、浏览器时，已经在跑就切
过去 —— 多开一个实例多半不是用户想要的。按住 Shift 点则照旧新开（调用方负责）。

只用 Win32（ctypes），不碰 Qt，零依赖。找不到、没权限、切换失败一律返回 False，
调用方照常启动 —— 所以这个功能**最坏情况就是退回原来的行为**。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

_u = ctypes.windll.user32 if sys.platform == "win32" else None
_k = ctypes.windll.kernel32 if sys.platform == "win32" else None

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SW_RESTORE = 9
_DWMWA_CLOAKED = 14

if _u is not None:
    _EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _u.EnumWindows.argtypes = [_EnumProc, wt.LPARAM]
    _u.GetWindow.restype = wt.HWND
    _u.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
    _u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
    _u.GetWindowThreadProcessId.restype = wt.DWORD
    _u.IsWindowVisible.argtypes = [wt.HWND]
    _u.IsIconic.argtypes = [wt.HWND]
    _u.GetWindowTextLengthW.argtypes = [wt.HWND]
    _u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    _u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    _u.SetForegroundWindow.argtypes = [wt.HWND]
    _u.GetForegroundWindow.restype = wt.HWND
    _u.BringWindowToTop.argtypes = [wt.HWND]
    _u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    _k.OpenProcess.restype = wt.HANDLE
    _k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                              ctypes.POINTER(wt.DWORD)]
    _k.CloseHandle.argtypes = [wt.HANDLE]


def supported():
    return _u is not None


def _exe_of_pid(pid):
    h = _k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""            # 以管理员身份运行的程序等：没权限查，当作没找到
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(len(buf))
        if _k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        _k.CloseHandle(h)


def _cloaked(hwnd):
    """被"隐身"的窗口（其他虚拟桌面、挂起的商店应用）：看得见的窗口列表里不算。"""
    try:
        v = wt.DWORD(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
        return bool(v.value)
    except Exception:
        return False


def _is_main_window(hwnd):
    """像任务栏那样挑"主窗口"：可见、没有所有者、不是工具窗、有标题、没隐身。"""
    if not _u.IsWindowVisible(hwnd):
        return False
    if _u.GetWindow(hwnd, _GW_OWNER):
        return False
    if _u.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
        return False
    if _u.GetWindowTextLengthW(hwnd) <= 0:
        return False
    return not _cloaked(hwnd)


def _broad_dir(d):
    """太"大"的目录：不能拿来做"同一个程序"的判断（下面什么程序都有）。"""
    d = os.path.normcase(os.path.abspath(d)).rstrip("\\")
    if len(d) <= 3:                                   # 盘符根目录
        return True
    sysroot = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if d == sysroot or d.startswith(sysroot + os.sep):
        return True
    roots = set()
    for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
              "LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        v = os.environ.get(k)
        if v:
            roots.add(os.path.normcase(os.path.abspath(v)).rstrip("\\"))
    return d in roots


def match_score(want, image):
    """进程 image 是不是快捷方式目标 want 那个程序：2 = 就是它，1 = 同一个程序，0 = 不是。

    1 的情况是"启动器 ≠ 真正进程"：快捷方式指向的 exe 只是个转发器，真正干活的
    进程在它所在目录的**子目录**里（Discord 的 Update.exe → app-1.0.9/Discord.exe；
    有些软件升级后主程序放进带版本号的子目录）。目标在系统目录、Program Files /
    用户目录根这类"大目录"时不用这条，否则下面什么程序都会被当成同一个。
    """
    if not want or not image:
        return 0
    want = os.path.normcase(os.path.abspath(want))
    image = os.path.normcase(os.path.abspath(image))
    if image == want:
        return 2
    wd = os.path.dirname(want)
    if not _broad_dir(wd) and image.startswith(wd + os.sep):
        return 1
    return 0


def find_window(exe_path):
    """找 exe_path 这个程序的主窗口；路径完全一致的优先，其次"同一个程序"的。

    同一档里取 Z 序最靠前的（EnumWindows 按 Z 序从前往后给）。没有返回 0。
    """
    if not supported() or not exe_path:
        return 0
    me = os.getpid()
    pid_exe = {}
    best = {2: 0, 1: 0}

    def cb(hwnd, _lp):
        try:
            if not _is_main_window(hwnd):
                return True
            pid = wt.DWORD(0)
            _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            p = pid.value
            if not p or p == me:
                return True
            if p not in pid_exe:
                pid_exe[p] = _exe_of_pid(p)
            sc = match_score(exe_path, pid_exe[p])
            if sc and not best[sc]:
                best[sc] = hwnd
                if sc == 2:
                    return False             # 已经是最好的了，不用再找
        except Exception:
            pass
        return True

    _u.EnumWindows(_EnumProc(cb), 0)
    return best[2] or best[1]


def activate(hwnd):
    """还原（如果最小化了）并切到前台，返回是否真的到了前台。

    **前台锁**：Windows 只允许"当前前台进程"把别的窗口提到最前，否则
    SetForegroundWindow 只会让任务栏图标闪一下。桌宠是工具窗口，点菜单时它未必是
    前台，所以被拒时用标准做法再试一次：把我们线程的输入队列临时挂到前台线程上
    （AttachThreadInput），这样系统就把我们当成前台的一部分，提完立刻解开。
    """
    try:
        if _u.IsIconic(hwnd):
            _u.ShowWindow(hwnd, _SW_RESTORE)
        if _u.SetForegroundWindow(hwnd) and _u.GetForegroundWindow() == hwnd:
            return True
        fg = _u.GetForegroundWindow()
        ft = _u.GetWindowThreadProcessId(fg, None) if fg else 0
        me = _k.GetCurrentThreadId()
        attached = bool(ft and ft != me and _u.AttachThreadInput(me, ft, True))
        try:
            _u.BringWindowToTop(hwnd)
            _u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _u.AttachThreadInput(me, ft, False)
        return _u.GetForegroundWindow() == hwnd
    except Exception:
        return False


def focus_running(exe_path):
    """exe 已经开着就切过去并返回 True；没开着返回 False（调用方照常启动）。

    **只要找到了窗口就返回 True**，哪怕没能切到前台：程序确实已经在运行，
    再启动一个正是这个功能要避免的。切不过去时系统也会闪任务栏图标提示。
    """
    hwnd = find_window(exe_path)
    if not hwnd:
        return False
    activate(hwnd)
    return True


# 这些本来就该多开（新开一个资源管理器窗口 / 一个新终端），切到旧的反而不对
_ALWAYS_NEW = {"explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe",
               "windowsterminal.exe", "conhost.exe", "mstsc.exe"}


def shift_held():
    """按住 Shift = 强制新开（和 Windows 任务栏同一个习惯）。"""
    try:
        return bool(_u.GetAsyncKeyState(0x10) & 0x8000)      # VK_SHIFT
    except Exception:
        return False


def try_focus(path, lnk_resolver=None):
    """快捷启动前调用：目标程序已开着就切过去并返回 True，否则 False。

    只管 .exe 和指向 .exe 的快捷方式；网址、文件夹、命令、文档一律 False。
    """
    if not supported() or shift_held():
        return False
    p = str(path or "")
    if p.lower().endswith(".lnk") and lnk_resolver is not None:
        try:
            p = lnk_resolver(p) or ""
        except Exception:
            p = ""
    if not p.lower().endswith(".exe") or not os.path.isfile(p):
        return False
    if os.path.basename(p).lower() in _ALWAYS_NEW:
        return False
    return focus_running(p)
