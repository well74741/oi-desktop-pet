# -*- coding: utf-8 -*-
"""聚合AI 应用窗口：用系统浏览器的 `--app` 模式打开各家大模型网页端。

为什么不再内置浏览器内核：
- QtWebEngine 是 Chromium 83（2020 年），占整个安装包四分之三，个别站点还得
  靠伪装 UA 才能进；
- 网页端的完整能力（联网搜索 / 深度研究 / 文件上传解析 / 画布 / 画图）都在浏览器
  里，换成 API 拿不到；
- `--app=URL` 打开的窗口**没有地址栏、没有标签页**，只有一条细标题栏，外观接近
  内嵌面板，而且可以被桌宠用 Win32 定位、置顶。

登录态存在桌宠自己的浏览器配置目录（`webchat_profile/`），与日常浏览器互不干扰：
每个站点首次登录一次，之后长期保持（和原来内嵌面板的行为一致）。
"""

import os
import subprocess
import sys

import data_store

_SITES_FILE = "webchat_sites.json"
_WIN_FILE = "webchat_windows.json"
_PROFILE_DIR = "webchat_profile"

_DEFAULT_SITES = [
    {"name": "DeepSeek", "url": "https://chat.deepseek.com"},
    {"name": "豆包", "url": "https://www.doubao.com/chat/"},
    {"name": "通义千问", "url": "https://www.qianwen.com/"},
    {"name": "Kimi", "url": "https://www.kimi.com/"},
    {"name": "文心一言", "url": "https://yiyan.baidu.com/"},
    {"name": "腾讯元宝", "url": "https://yuanbao.tencent.com/chat"},
    {"name": "讯飞星火", "url": "https://xinghuo.xfyun.cn/desk"},
]

# 优先 Edge：Win10/11 必定自带；其次 Chrome。两者都是 Chromium，--app 行为一致。
_BROWSERS = [
    ("Edge", [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"]),
    ("Chrome", [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"]),
]

DEFAULT_SIZE = (960, 720)


# ==================== 站点列表 ====================

def load_sites():
    """站点列表：用户自定义优先，内置默认项按名字补齐（与旧版行为一致）。"""
    sites = data_store.read_list(_SITES_FILE)
    if not sites:
        return list(_DEFAULT_SITES)
    out, seen = [], set()
    for s in sites:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        if not url:
            continue
        name = str(s.get("name") or url).strip()
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "url": url})
    for d in _DEFAULT_SITES:
        if d["name"] not in seen:
            out.append(dict(d))
            seen.add(d["name"])
    return out


def save_sites(sites):
    data_store.write_json(_SITES_FILE, list(sites or []))


def _win_cfg():
    cfg = data_store.read_dict(_WIN_FILE) if hasattr(data_store, "read_dict") else None
    return cfg if isinstance(cfg, dict) else {}


def _save_win_cfg(cfg):
    data_store.write_json(_WIN_FILE, cfg)


def on_top():
    return bool(_win_cfg().get("on_top", False))


def set_on_top(flag):
    """置顶开关；作用在宿主窗口上（Qt 那边读 on_top() 应用），这里只存。"""
    cfg = _win_cfg()
    cfg["on_top"] = bool(flag)
    _save_win_cfg(cfg)


def last_site():
    """上次用的站点名；模块按钮「打开」直接开它，不再弹列表让人选。"""
    v = _win_cfg().get("last")
    return str(v) if v else None


def set_last_site(name):
    key = str(name or "")
    cfg = _win_cfg()
    if cfg.get("last") == key:
        return          # 每次聚焦都写盘没必要
    cfg["last"] = key
    _save_win_cfg(cfg)


# ==================== 浏览器 ====================

def browser_path():
    """返回 (可执行文件, 名称)；找不到返回 (None, None)。"""
    if sys.platform != "win32":
        return (None, None)
    for name, cands in _BROWSERS:
        for c in cands:
            p = os.path.expandvars(c)
            if "%" not in p and os.path.exists(p):
                return (p, name)
    return (None, None)


def profile_dir():
    """桌宠专用的浏览器配置目录（登录态存这里，不碰你日常浏览器）。

    放 %LOCALAPPDATA%/oi桌宠/ 而不是 exe 旁边，两个原因：
    ① 安装版装在 Program Files，普通用户没有写权限，配置目录根本建不起来；
    ② 浏览器配置目录会长到几百 MB（缓存），不该塞进安装目录。
    取不到 LOCALAPPDATA 时退回数据目录（源码模式就是项目根目录）。
    """
    base = os.environ.get("LOCALAPPDATA") or ""
    if base and os.path.isdir(base):
        return os.path.join(base, "oi桌宠", _PROFILE_DIR)
    return data_store.data_path(_PROFILE_DIR)


# ==================== Win32 窗口 ====================

def _win32():
    import ctypes
    import ctypes.wintypes as wt
    return ctypes.windll.user32, ctypes, wt


def _profile_pids():
    """正在使用桌宠配置目录的浏览器主进程 PID（排除 --type= 的子进程）。

    同一个 user-data-dir 第二次启动时，新进程会把请求转交给已有实例后自己退出，
    所以不能按"我们启动的那个进程"去找窗口，只能按配置目录反查真正的主进程。
    """
    prof = profile_dir().lower()
    pids = set()
    try:
        import ctypes
        import ctypes.wintypes as wt
        k = ctypes.windll.kernel32

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_char * 260)]

        exe_names = {b"msedge.exe", b"chrome.exe"}
        snap = k.CreateToolhelp32Snapshot(0x2, 0)
        e = PE32()
        e.dwSize = ctypes.sizeof(PE32)
        cand = []
        if k.Process32First(snap, ctypes.byref(e)):
            while True:
                if e.szExeFile.lower() in exe_names:
                    cand.append(e.th32ProcessID)
                if not k.Process32Next(snap, ctypes.byref(e)):
                    break
        k.CloseHandle(snap)
        if not cand:
            return pids
        for pid, cmd in _cmdlines(cand).items():
            low = cmd.lower()
            if prof in low and "--type=" not in low:
                pids.add(pid)
    except Exception:
        pass
    return pids


def _cmdlines(pids):
    """批量取进程命令行（WMI 一次查询，避免逐个起 powershell）。"""
    out = {}
    try:
        flt = " or ".join("ProcessId=%d" % p for p in pids)
        ps = ("Get-CimInstance Win32_Process -Filter \"%s\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }" % flt)
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000)
        for line in (res.stdout or "").splitlines():
            if "\t" in line:
                pid, cmd = line.split("\t", 1)
                if pid.strip().isdigit():
                    out[int(pid.strip())] = cmd
    except Exception:
        pass
    return out


def _all_browser_windows():
    """所有可见的 Chromium 顶层窗口（含别家 Electron 应用）。

    只用于"启动前后做差集"：几秒内新出现的窗口就是刚开的那个。比逐轮查进程
    命令行快两个数量级。
    """
    u, ctypes, wt = _win32()
    found = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin"):
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 200 and r.bottom - r.top > 150:
                    found.add(h)
        return True

    u.EnumWindows(cb, 0)
    return found


def _windows_of(pids):
    """这些进程的可见顶层浏览器窗口 hwnd 集合。"""
    if not pids:
        return set()
    u, ctypes, wt = _win32()
    found = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin"):
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 200 and r.bottom - r.top > 150:
                    found.add(h)
        return True

    u.EnumWindows(cb, 0)
    return found


_TRACK = {}          # site key -> hwnd（进程内缓存）
_ACTIVE = [None]     # 最近打开/聚焦的站点名（侧边栏据此高亮与跟随）


def _is_live_browser_window(hwnd):
    """句柄还活着、且确实是浏览器窗口。

    只用这两项做校验（不查进程命令行）：查命令行要起 PowerShell，约 1 秒，
    放在"点一下切换"的路径上会明显卡顿。类名校验是为了防句柄被回收后
    误认成别的窗口。
    **不查 IsWindowVisible**：窗口刚开出来时是我们主动藏起来的（等宿主摆好
    再显示），嵌进宿主后也可能随宿主最小化——拿可见性当"活着"会把它判死。
    """
    try:
        u, ctypes, wt = _win32()
        if not u.IsWindow(hwnd):
            return False
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        return cls.value.startswith("Chrome_WidgetWin")
    except Exception:
        return False


def _tracked():
    """站点 -> 窗口句柄，并剔除已经关掉的。"""
    for key in [k for k, h in _TRACK.items() if not _is_live_browser_window(h)]:
        _TRACK.pop(key, None)
    return dict(_TRACK)


def _apply_on_top(hwnd, flag):
    try:
        u, ctypes, wt = _win32()
        # hWndInsertAfter 是句柄（指针宽度）。直接传 -1 会被当成 32 位 int，
        # 64 位下 SetWindowPos 收到的是无效句柄，置顶会静默失败。
        HWND_TOPMOST = ctypes.c_void_p(-1)
        HWND_NOTOPMOST = ctypes.c_void_p(-2)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        u.SetWindowPos(ctypes.c_void_p(hwnd),
                       HWND_TOPMOST if flag else HWND_NOTOPMOST, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    except Exception:
        pass


def _focus(hwnd):
    try:
        u, ctypes, wt = _win32()
        SW_RESTORE = 9
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
    except Exception:
        pass


def preferred_size():
    """宿主窗口尺寸（Qt 逻辑像素）：上次用户调整后的大小，没有就用默认。"""
    size = _win_cfg().get("size")
    if isinstance(size, list) and len(size) == 2:
        try:
            return (max(400, int(size[0])), max(300, int(size[1])))
        except Exception:
            pass
    return DEFAULT_SIZE


def set_preferred_size(w, h):
    """记住用户调整后的宿主窗口尺寸，下次开在同样大小。"""
    try:
        w, h = int(w), int(h)
        if w < 300 or h < 200:
            return
        cfg = _win_cfg()
        if cfg.get("size") == [w, h]:
            return
        cfg["size"] = [w, h]
        _save_win_cfg(cfg)
    except Exception:
        pass


def compute_placement(near, work, size, gap=12):
    """算出窗口该摆在哪：贴着桌宠、放在空间较大的一侧、垂直居中并夹进工作区。

    全部是纯数字运算（near/work 都是 (x, y, w, h)），不碰 Qt。
    """
    w, h = size
    wx, wy, ww, wh = work
    wr, wb = wx + ww, wy + wh
    if near is None:
        return (wx + (ww - w) // 2, wy + (wh - h) // 2, w, h)
    nx, ny, nw, nh = near
    nr, ncy = nx + nw, ny + nh // 2
    if (wr - nr) >= (nx - wx):
        x = min(nr + gap, wr - w)
    else:
        x = max(nx - gap - w, wx)
    y = ncy - h // 2
    x = max(wx, min(x, wr - w))
    y = max(wy, min(y, wb - h))
    return (int(x), int(y), int(w), int(h))


def tracked():
    """当前已打开的站点 -> 窗口句柄（只读副本）。"""
    return _tracked()


def active():
    """当前活动站点 (名字, hwnd)；没有则 (None, None)。"""
    live = _tracked()
    key = _ACTIVE[0]
    if key in live:
        return (key, live[key])
    if live:
        key = sorted(live)[0]
        _ACTIVE[0] = key
        return (key, live[key])
    return (None, None)


def window_rect(hwnd):
    """窗口的屏幕矩形 (x, y, w, h)；取不到返回 None。"""
    try:
        u, ctypes, wt = _win32()
        r = wt.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


def window_alive(hwnd):
    """窗口还在、还是浏览器窗口（最小化也算活着，子窗口跟着一起收）。"""
    return _is_live_browser_window(hwnd)


def _monitor_work_area(near):
    """near 所在显示器的工作区 (x, y, w, h)；取不到退回主屏。

    这里不用 Qt（本模块是纯 Win32 后端），直接问 MonitorFromPoint + GetMonitorInfo。
    """
    try:
        u, ctypes, wt = _win32()

        class _MI(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", ctypes.c_ulong)]

        if near:
            px = int(near[0] + near[2] // 2)
            py = int(near[1] + near[3] // 2)
        else:
            px = py = 0
        pt = ctypes.c_longlong((py << 32) | (px & 0xFFFFFFFF))
        mon = u.MonitorFromPoint(pt, 2)        # MONITOR_DEFAULTTONEAREST
        mi = _MI()
        mi.cbSize = ctypes.sizeof(_MI)
        if mon and u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            r = mi.rcWork
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        return (0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1))
    except Exception:
        return (0, 0, 1920, 1080)


def restore_browser(hwnd, near=None):
    """把网页窗口从"隐藏/最小化"状态恢复到屏内正常位置。

    `open_site` 是先 `--hide` 开出来再交给上层摆位的；旧架构由宿主负责，
    贴边栏模式下没有宿主，就得这里自己来——不恢复的话窗口停在
    (-32000, -32000)，用户什么都看不到。

    位置：有 `near`（桌宠矩形）就按 `compute_placement` 摆在它旁边，
    否则保持窗口自己的尺寸、居中到当前屏幕。
    """
    try:
        u, ctypes, wt = _win32()
        SW_SHOWNOACTIVATE, SW_RESTORE = 4, 9
        if u.IsIconic(ctypes.c_void_p(hwnd)):
            u.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
        r = window_rect(hwnd)
        w, h = (r[2], r[3]) if r else preferred_size()
        work = _monitor_work_area(near)
        x, y, w, h = compute_placement(near, work, (w, h))
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y),
                       int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)
        u.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
        return True
    except Exception:
        return False


def place_browser(hwnd, rect):
    """把网页窗口摆到指定矩形上（切站点时拿上一个窗口的矩形来"就地换内容"）。

    贴边栏模式下每个站点是**各自一个窗口**，不摆的话新窗口会按
    `compute_placement` 落在别处 —— 用户看到的就是"切模型弹出了个新页面"。
    摆到同一个矩形上，再把上一个藏掉，才像标签页那样原地换内容。
    """
    if not rect:
        return False
    try:
        u, ctypes, wt = _win32()
        SW_RESTORE = 9
        if u.IsIconic(ctypes.c_void_p(hwnd)):
            u.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
        x, y, w, h = (int(v) for v in rect[:4])
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, w, h,
                       SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:
        return False


def hide_others(keep):
    """把除 `keep` 以外所有由桌宠打开的网页窗口藏起来（**不关**）。

    旧架构里这件事是宿主 `_reveal` 做的；贴边栏模式没有宿主，一开始漏了，
    结果每切一次模型就多一个顶层窗口摊在桌面上（用户反馈"切换其他模型会
    弹出新页面"）。不关的理由见 `hide_browser`：切回去是瞬间的，页面状态和
    写了一半的提问都还在，就是标签页。
    """
    n = 0
    for _key, h in list(_tracked().items()):
        if h == keep:
            continue
        if window_visible(h):
            hide_browser(h)
            n += 1
    return n


def window_visible(hwnd):
    """窗口当前是不是显示着的（被 SW_HIDE 藏起来的返回 False）。

    宿主用它守"当前页面必须看得见"这条不变式——接管链路半路作废时页面会一直
    藏着，主区域就是一片空白。
    """
    try:
        u, ctypes, wt = _win32()
        return bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))
    except Exception:
        return True      # 问不出来就当它是可见的，别反复去 show


def dpi_scale_at(x, y):
    """含物理坐标点 (x, y) 的显示器缩放比（1.0 / 1.25 / 1.5 …）。

    浏览器命令行的 `--window-position/--window-size` 吃的是 DIP（逻辑像素），
    而我们算出来的位置是物理像素，不换算的话 150% 屏上窗口会大出一半。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        MONITOR_DEFAULTTONEAREST = 2
        u.MonitorFromPoint.restype = ctypes.c_void_p
        hmon = u.MonitorFromPoint(wt.POINT(int(x), int(y)),
                                  MONITOR_DEFAULTTONEAREST)
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(
                ctypes.c_void_p(hmon), 0, ctypes.byref(dx),
                ctypes.byref(dy)) == 0 and dx.value:
            return max(0.5, min(4.0, dx.value / 96.0))
    except Exception:
        pass
    return 1.0


# ==================== 把侧边栏塞进网页窗口里 ====================
#
# 侧边栏以前是"贴在浏览器左边的另一个窗口"，无论怎么追都有两个毛病：位置靠
# 定时器同步，拖窗口必然慢半拍；而且它在浏览器窗口**外面**，视觉上就是两块。
# 现在用 SetParent 把它变成浏览器窗口的**真子窗口**：坐标由系统按父窗口客户区
# 算，移动/缩放/最小化零延迟跟随，永远不可能分离。代价是它浮在网页上方，会盖
# 住页面最左边一条。
#
# 跨进程 SetParent 要先把窗口样式从 WS_POPUP 改成 WS_CHILD，否则只会变成"被
# 拥有的弹窗"，既不裁剪也不跟随。

_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_CLIPSIBLINGS = 0x04000000


def _as_long(v):
    """32 位无符号样式位 → ctypes 的有符号 LONG。"""
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


def _style_api():
    u, ctypes, wt = _win32()
    # GWL_STYLE 永远是 32 位，64 位下也用 GetWindowLongW，不必上 Ptr 版
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    u.SetParent.restype = ctypes.c_void_p
    u.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return u, ctypes, wt




def client_size(hwnd):
    """窗口客户区尺寸 (w, h)，物理像素——子窗口的坐标就是按它算的。"""
    try:
        u, ctypes, wt = _win32()
        r = wt.RECT()
        if not u.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None
        return (r.right, r.bottom)
    except Exception:
        return None



def place_child(child, rect, top=False):
    """把子窗口摆到父窗口客户区的 rect；top=True 时顺便提到同级最上面。"""
    try:
        u, ctypes, wt = _win32()
        x, y, w, h = rect
        HWND_TOP = ctypes.c_void_p(0)
        SWP_NOACTIVATE, SWP_NOZORDER = 0x0010, 0x0004
        flags = SWP_NOACTIVATE | (0 if top else SWP_NOZORDER)
        u.SetWindowPos(ctypes.c_void_p(child), HWND_TOP,
                       int(x), int(y), int(w), int(h), flags)
        return True
    except Exception:
        return False


# ==================== 把网页窗口"粘"到桌宠自己的窗口上 ====================
#
# 宿主用 Qt 布局排"侧边栏 + 网页容器"，网页窗口盖在容器那块区域上，于是侧边栏
# 占的是真实布局空间，网页被挤窄而不是被盖住——这才是"和网页一体、不遮挡"。
#
# 网页窗口**必须保持顶层窗口**，不能 SetParent 成宿主的子窗口。子窗口那版
# 表面上更完美（跟随零延迟、自动裁剪），但中文输入法会废掉：跨进程 SetParent
# 会让"活动窗口"属于桌宠线程、而"键盘焦点"属于浏览器线程，而 Windows 的输入法
# UI 是按**活动窗口所在线程**走的，够不到网页——输入法于是退化成自己那套浮动
# 候选窗，飘在屏幕角上（实测 GetGUIThreadInfo：active 在桌宠线程 30000、
# focus 在浏览器线程 23372）。顶层 + owner 的组合下，active 和 focus 都在浏览器
# 线程，和独立浏览器窗口完全一致，输入法候选窗就正常跟着光标。
#
# 所以这里靠三样东西把它"粘"住：
#   1. owner（`GWL_HWNDPARENT`）—— z 序永远在宿主之上，随宿主一起最小化/关闭，
#      也不占任务栏和 Alt+Tab；
#   2. 每次宿主 move/resize 都跟着摆位（跟随由 UI 层负责）；
#   3. 窗口区域（`SetWindowRgn`）—— Chromium 的 `--app` 窗口自己在客户区里画
#      标题栏，Win32 去不掉；把窗口往外撑、再用窗口区域只露出内容那一块，标题栏
#      就被裁掉了（子窗口那版是靠父窗口裁的，顶层窗口只能自己裁）。

_WS_CLIPCHILDREN = 0x02000000
_GWL_EXSTYLE = -20
_GWL_HWNDPARENT = -8
_WS_EX_APPWINDOW = 0x00040000


def _find_descendant(parent, cls_prefix):
    """在窗口的所有后代里找第一个类名以 cls_prefix 开头的。"""
    try:
        u, ctypes, wt = _win32()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, _):
            buf = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(h, buf, 128)
            if buf.value.startswith(cls_prefix):
                found.append(h)
                return False
            return True

        u.EnumChildWindows(ctypes.c_void_p(parent), cb, 0)
        return found[0] if found else None
    except Exception:
        return None


_INSETS_CACHE = {}


def browser_insets(hwnd, timeout=0.0):
    """网页内容区相对窗口矩形的四边内缩 (左, 上, 右, 下)，物理像素。

    直接问渲染子窗口（`Chrome_RenderWidgetHostHWND`）在哪，比猜标题栏常数准，
    也自动跟着 DPI 走。量不到返回 None。

    量到的值会按 hwnd 记一份：缩放/切站点途中渲染子窗口可能一瞬间查不到，
    这时宁可用上一次的好值，也不能退回 (0,0,0,0)——见 `last_insets()`。
    """
    import time
    t0 = time.monotonic()
    while True:
        rw = _find_descendant(hwnd, "Chrome_RenderWidgetHostHWND")
        if rw:
            wr, rr = window_rect(hwnd), window_rect(rw)
            if wr and rr and rr[2] > 0 and rr[3] > 0:
                l = rr[0] - wr[0]
                t = rr[1] - wr[1]
                r = (wr[0] + wr[2]) - (rr[0] + rr[2])
                b = (wr[1] + wr[3]) - (rr[1] + rr[3])
                if 0 <= l < 120 and 0 <= t < 240 and 0 <= r < 120 and 0 <= b < 120:
                    _INSETS_CACHE[int(hwnd)] = (l, t, r, b)
                    return (l, t, r, b)
        if time.monotonic() - t0 >= timeout:
            return None
        time.sleep(0.03)


def last_insets(hwnd):
    """这个窗口上一次量到的内缩；从没量到过返回 None。"""
    return _INSETS_CACHE.get(int(hwnd))


def forget_insets(hwnd):
    _INSETS_CACHE.pop(int(hwnd), None)


def glue_browser(hwnd, owner):
    """把网页窗口"粘"到宿主窗口上：保持顶层，只设 owner。

    **不要改成 SetParent 子窗口**——那样中文输入法的候选浮窗会飘（见本节顶部
    的说明）。owner 关系给的是：z 序压在宿主之上、随宿主一起最小化、不单独
    占任务栏/Alt+Tab。位置和裁剪由 `fit_browser()` 负责。
    """
    try:
        u, ctypes, wt = _style_api()
        # 确保是顶层弹出窗口（万一之前被做成过子窗口）
        st = u.GetWindowLongW(ctypes.c_void_p(hwnd), _GWL_STYLE) & 0xFFFFFFFF
        if st & _WS_CHILD:
            st = (st & ~_WS_CHILD) | _WS_POPUP
            u.SetWindowLongW(ctypes.c_void_p(hwnd), _GWL_STYLE, _as_long(st))
            u.SetParent(ctypes.c_void_p(hwnd), None)
        # 去掉任务栏按钮：现在它只是宿主窗口的一部分，不该单独占一格
        ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), _GWL_EXSTYLE) & 0xFFFFFFFF
        u.SetWindowLongW(ctypes.c_void_p(hwnd), _GWL_EXSTYLE,
                         _as_long(ex & ~_WS_EX_APPWINDOW))
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW(ctypes.c_void_p(hwnd), _GWL_HWNDPARENT,
                            ctypes.c_void_p(owner))
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
        SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020
        u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                       | SWP_FRAMECHANGED)
        return owner_of(hwnd) == owner
    except Exception:
        return False


def owner_of(hwnd):
    """窗口的 owner（没有返回 0）。"""
    try:
        u, ctypes, wt = _win32()
        u.GetWindow.restype = ctypes.c_void_p
        return u.GetWindow(ctypes.c_void_p(hwnd), 4) or 0   # GW_OWNER
    except Exception:
        return 0


def fit_browser(hwnd, holder, insets=None):
    """把网页内容摆到正好压住 holder，并用窗口区域把标题栏裁掉。

    holder 的**屏幕**矩形就是网页该占的位置（网页窗口是顶层窗口，坐标是屏幕
    坐标）。窗口往左上挪、往外撑 insets，内容区就正好落在 holder 上；多出来的
    标题栏/边框用 `SetWindowRgn` 裁掉——顶层窗口没有父窗口帮它裁，只能自己裁。

    **绝不用 (0,0,0,0) 兜底**：零内缩等于"这窗口没有标题栏"，于是不挪不裁，
    浏览器自己那条标题栏就占住内容区顶部、整页往下错位，侧边栏也会被盖掉。
    缩放或切站点的一瞬间渲染子窗口查不到是常事，这时用上次量到的好值。

    **这个函数必须是非阻塞的**：`WebChatHost.moveEvent` 每一帧都调它，
    在这里同步等待（哪怕只有 0.25s）会把主线程卡死——拖动聚合AI 窗口时整个桌宠
    僵住、网页跟不上容器，看起来就是"页面和侧边栏分离了"。量不到就直接返回，
    让调用方安排一次异步重试。
    """
    try:
        rect = window_rect(holder)
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            return None
        # timeout 一律为 0：绝不在这条路径上等
        ins = insets or browser_insets(hwnd) or last_insets(hwnd)
        if ins is None:
            return None
        l, t, r, b = ins
        x, y, w, h = rect
        place_child(hwnd, (x - l, y - t, w + l + r, h + t + b))
        _clip_to_content(hwnd, ins, w, h)
        return ins
    except Exception:
        return None


def _clip_to_content(hwnd, insets, w, h):
    """窗口区域只留内容那一块（窗口内坐标），标题栏和边框被裁掉。"""
    try:
        u, ctypes, wt = _win32()
        g32 = ctypes.windll.gdi32
        g32.CreateRectRgn.restype = ctypes.c_void_p
        l, t, _r, _b = insets
        rgn = g32.CreateRectRgn(int(l), int(t), int(l + w), int(t + h))
        if not rgn:
            return False
        # SetWindowRgn 成功之后区域归系统所有，不要自己 DeleteObject
        return bool(u.SetWindowRgn(ctypes.c_void_p(hwnd),
                                   ctypes.c_void_p(rgn), True))
    except Exception:
        return False


def content_rect(hwnd):
    """网页内容区（渲染子窗口）的屏幕矩形；量不到返回 None。"""
    rw = _find_descendant(hwnd, "Chrome_RenderWidgetHostHWND")
    return window_rect(rw) if rw else None


def fit_ok(hwnd, holder, tol=3):
    """网页内容是不是已经正好对上容器了。

    调用方据此决定"现在能不能把它显示出来"——摆好之前就显示的话，浏览器
    自己那条标题栏（连同最小化/关闭按钮）会露出来闪一下，非常难看。
    """
    c, h = content_rect(hwnd), window_rect(holder)
    if not c or not h or c[2] <= 0 or c[3] <= 0:
        return False
    return (abs(c[0] - h[0]) <= tol and abs(c[1] - h[1]) <= tol
            and abs(c[2] - h[2]) <= tol and abs(c[3] - h[3]) <= tol)


def unglue_browser(hwnd):
    """解开与宿主的 owner 关系、去掉窗口区域，还原成一个正常的独立窗口。"""
    forget_insets(hwnd)     # HWND 会被系统回收复用，别让下一个窗口继承旧内缩
    try:
        u, ctypes, wt = _style_api()
        st = u.GetWindowLongW(ctypes.c_void_p(hwnd), _GWL_STYLE) & 0xFFFFFFFF
        if st & _WS_CHILD:
            st = (st & ~_WS_CHILD) | _WS_POPUP
            u.SetWindowLongW(ctypes.c_void_p(hwnd), _GWL_STYLE, _as_long(st))
            u.SetParent(ctypes.c_void_p(hwnd), None)
        u.SetWindowRgn(ctypes.c_void_p(hwnd), None, True)   # 去掉裁剪
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW(ctypes.c_void_p(hwnd), _GWL_HWNDPARENT, None)
        return True
    except Exception:
        return False


def focus_browser(hwnd):
    """把键盘输入交给网页。

    网页窗口是**顶层**窗口、和桌宠不是一个进程，`SetFocus` 跨线程是无效的
    （输入队列没有挂接），必须让它成为前台窗口——这也正是中文输入法能正常
    工作的前提：活动窗口和键盘焦点都落在浏览器那个线程上。
    """
    try:
        u, ctypes, wt = _win32()
        if not _is_live_browser_window(hwnd):
            return False
        u.SetForegroundWindow(ctypes.c_void_p(hwnd))
        rw = _find_descendant(hwnd, "Chrome_RenderWidgetHostHWND") or hwnd
        u.SetFocus(ctypes.c_void_p(rw))
        return True
    except Exception:
        return False


def window_title(hwnd):
    """窗口标题（网页标题）；宿主拿它当自己的标题显示。"""
    try:
        u, ctypes, wt = _win32()
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""



def focus(hwnd):
    """把窗口提到前台（公开给侧边栏用）。"""
    _focus(hwnd)



def close_all():
    """关掉所有由桌宠打开的网页窗口（发 WM_CLOSE，浏览器自己保存会话）。"""
    u, ctypes, wt = _win32()
    WM_CLOSE = 0x0010
    for key, hwnd in list(_tracked().items()):
        try:
            u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
        _TRACK.pop(key, None)


# ==================== 打开站点 ====================

def open_site(site, size=None, timeout=20.0):
    """打开（或聚焦）一个站点的应用窗口，返回 (成功?, 错误信息, hwnd)。

    site: {"name":..., "url":...}；size: (w, h) 网页容器的物理像素尺寸，
    让浏览器一上来就按这个大小排版，省掉塞进宿主后再回流一次。
    可在后台线程调用（内部只用 Win32 与 subprocess，不碰 Qt）。
    窗口**不做摆位**：调用方会把它塞进桌宠的宿主窗口里，位置由宿主布局决定。
    """
    import time

    url = str((site or {}).get("url") or "").strip()
    if not url:
        return (False, "站点没有网址", None)
    key = str((site or {}).get("name") or url)

    live = _tracked()
    hwnd = live.get(key)
    if hwnd:
        _ACTIVE[0] = key
        set_last_site(key)
        return (True, "", hwnd)

    exe, _name = browser_path()
    if not exe:
        return (False, "没找到 Edge 或 Chrome。聚合AI 用系统浏览器的应用窗口模式"
                       "打开网页版大模型，请先安装其中之一。", None)

    before = _all_browser_windows()
    w, h = size or (960, 720)
    try:
        os.makedirs(profile_dir(), exist_ok=True)
    except Exception:
        pass
    # 直接生在屏幕外：窗口一出现就要被我们塞进宿主，中间这几十毫秒不该让人
    # 看见它在桌面上闪一下。命令行吃的是 DIP，按主屏缩放比换算尺寸。
    s = dpi_scale_at(0, 0)
    args = [exe, "--app=" + url,
            "--user-data-dir=" + profile_dir(),
            "--no-first-run", "--no-default-browser-check",
            "--window-position=-32000,-32000",
            "--window-size=%d,%d" % (max(200, round(w / s)),
                                     max(200, round(h / s)))]
    try:
        # cwd 不能留给它继承我们的安装目录：浏览器会按 DLL 搜索顺序查当前目录，
        # 可能从 `_internal\` 里拿 VCRUNTIME140.dll 之类的公共运行时并占住句柄，
        # 之后装新版就替换不了那个文件（报 DeleteFile failed; code 5）。
        # 用浏览器自己所在的目录——它本来就期望这样被启动。
        _cwd = os.path.dirname(os.path.abspath(exe)) if exe else None
        subprocess.Popen(args, creationflags=0x08000000,
                         cwd=_cwd if _cwd and os.path.isdir(_cwd) else None)
    except Exception as e:
        return (False, "启动浏览器失败：%s" % e, None)

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        fresh = _all_browser_windows() - before
        if fresh:
            hwnd = sorted(fresh)[0]
            if len(fresh) > 1:
                # 同一瞬间冒出好几个 Chromium 窗口才值得花一秒去查进程命令行。
                # 以前**每次**都查，而查完才动窗口——窗口就在错的位置上干等
                # 一秒，这正是"先出现在左上角再弹回来"的真正原因。
                try:
                    ours = fresh & _windows_of(_profile_pids())
                    if ours:
                        hwnd = sorted(ours)[0]
                except Exception:
                    pass
            _hide(hwnd)        # 先藏起来，等宿主把它摆好再显示
            _TRACK[key] = hwnd
            _ACTIVE[0] = key
            set_last_site(key)
            return (True, "", hwnd)
        time.sleep(0.01)
    return (False, "窗口没有在 %d 秒内出现，可能被浏览器拦截或启动过慢。"
                   % int(timeout), None)


def _hide(hwnd):
    try:
        u, ctypes, wt = _win32()
        u.ShowWindow(ctypes.c_void_p(hwnd), 0)      # SW_HIDE
    except Exception:
        pass


def hide_browser(hwnd):
    """把某个网页收起来（切到别的站点时用）。

    注意**不要关它**：网页窗口嵌在宿主里的时候，关掉其中一个会把整个浏览器
    进程带走（实测：不嵌入时关一个没事，嵌入后关一个整个进程退出，剩下的
    窗口跟着没）。藏起来还有额外好处——切回去是瞬间的，页面状态、正在写的
    提问都还在，跟浏览器标签页一样。
    """
    _hide(hwnd)


def show_browser(hwnd):
    """摆好之后再显示（SW_SHOWNA：不抢焦点），顺便提到同级最上面。

    提层是因为切站点时旧页面还显示着（不留空白），新页面得盖在它上面。
    """
    try:
        u, ctypes, wt = _win32()
        u.ShowWindow(ctypes.c_void_p(hwnd), 8)      # SW_SHOWNA
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass
