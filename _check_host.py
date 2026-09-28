# -*- coding: utf-8 -*-
"""聚合AI 宿主窗口验真：用**真实的 Edge/Chrome `--app` 窗口**跑一遍完整路径，
确认网页是被侧边栏"挤窄"而不是"盖住"，切站点复用同一个窗口，键盘能输入。

用临时 user-data-dir（跑完就删），**不碰桌宠的 webchat_profile、不用任何已
登录会话**；开的是 example.com / example.org 两个中性页面。
用法：python _check_host.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

u = ctypes.windll.user32
u.GetParent.restype = ctypes.c_void_p
u.GetParent.argtypes = [ctypes.c_void_p]


class _GTI(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", wt.RECT)]


def _gui_thread_info():
    """前台线程的 (活动窗口, 焦点窗口)；取不到返回 None。"""
    g = _GTI()
    g.cbSize = ctypes.sizeof(g)
    if not u.GetGUIThreadInfo(0, ctypes.byref(g)):
        return None
    return (g.hwndActive, g.hwndFocus)


def _tid(h):
    pid = wt.DWORD()
    return u.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))

from PyQt5.QtWidgets import QApplication            # noqa: E402

app = QApplication([])

import webchat_launcher as L                        # noqa: E402
import webchat_ui                                   # noqa: E402
from widgets import kit                             # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def pump(sec):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


exe, brand = L.browser_path()
if not exe:
    print("没找到 Edge / Chrome，跳过")
    sys.exit(0)
print("用", brand)

profile = tempfile.mkdtemp(prefix="oi_host_probe_")
_real_profile = L.profile_dir
L.profile_dir = lambda: profile
L.save_sites = lambda s: None            # 绝不写用户数据
L.set_last_site = lambda n: None
L.set_preferred_size = lambda w, h: None
SITES = [{"name": "甲站", "url": "https://example.com"},
         {"name": "乙站", "url": "https://example.org"}]
L.load_sites = lambda: [dict(s) for s in SITES]

host = None
try:
    t0 = time.monotonic()
    webchat_ui.open_site_ui(SITES[0], near=(200, 300, 80, 80))
    host = webchat_ui.host()
    dl = time.monotonic() + 40
    while time.monotonic() < dl and host._hwnd is None:
        pump(0.1)
    check("聚合AI 窗口开起来并接管了网页", host._hwnd is not None,
          "%.2fs" % (time.monotonic() - t0))
    if host._hwnd is None:
        raise SystemExit(1)
    pump(1.5)

    hwnd = host._hwnd
    holder = host.holder_hwnd()
    # 网页窗口必须**保持顶层**、只靠 owner 粘住宿主：SetParent 成子窗口的话
    # 活动窗口会归桌宠线程、键盘焦点归浏览器线程，中文输入法的候选浮窗就废了
    check("网页窗口仍是顶层窗口（没被 SetParent 成子窗口）",
          not u.GetParent(ctypes.c_void_p(hwnd)))
    check("网页窗口的 owner 是宿主（z 序粘住、随宿主一起最小化）",
          L.owner_of(hwnd) == int(host.winId()))

    host.refit()
    pump(0.4)
    rw = L._find_descendant(hwnd, "Chrome_RenderWidgetHostHWND")
    rr, hr = L.window_rect(rw), L.window_rect(holder)
    check("网页内容正好铺满容器（标题栏被裁在外面）",
          abs(rr[0] - hr[0]) <= 2 and abs(rr[1] - hr[1]) <= 2
          and abs(rr[2] - hr[2]) <= 3 and abs(rr[3] - hr[3]) <= 3,
          "内容=%s 容器=%s" % (rr, hr))

    # 这条是这次改动的重点：网页不能被侧边栏盖住
    bar = host.bar
    br = (bar.mapToGlobal(bar.rect().topLeft()),
          bar.mapToGlobal(bar.rect().bottomRight()))
    bar_right_phys = L.window_rect(int(host.winId()))[0] + \
        int(round(bar.width() * host.devicePixelRatioF()))
    check("侧边栏和网页不重叠（网页被挤窄，不是被盖住）",
          rr[0] >= bar_right_phys - 3,
          "网页 left=%d 侧边栏右缘=%d 栏宽=%d(逻辑)"
          % (rr[0], bar_right_phys, bar.width()))
    check("侧边栏占的是真实布局宽度",
          bar.width() == kit.ui(bar.PANEL_W)
          and host.holder.x() >= bar.width(),
          "bar=%d holder.x=%d" % (bar.width(), host.holder.x()))

    # 缩放宿主 → 网页跟着重排
    host.resize(820, 560)
    pump(0.8)
    rr2, hr2 = L.window_rect(rw), L.window_rect(holder)
    check("宿主缩放后网页跟着重排",
          abs(rr2[2] - hr2[2]) <= 3 and abs(rr2[3] - hr2[3]) <= 3,
          "内容=%s 容器=%s" % (rr2, hr2))

    # 移动宿主 → 网页跟着摆（顶层窗口，靠 moveEvent 里自己摆位）
    host.move(420, 260)
    pump(0.5)
    rr3, hr3 = L.window_rect(rw), L.window_rect(holder)
    check("宿主移动后网页跟着一起走（moveEvent 里立刻摆位，不防抖）",
          abs(rr3[0] - hr3[0]) <= 2 and abs(rr3[1] - hr3[1]) <= 2)

    # 焦点：能打字
    host.activateWindow()
    host.raise_()
    pump(0.5)
    L.focus_browser(hwnd)
    pump(0.3)
    check("键盘焦点在网页上（输入框能打字）", u.GetFocus() in (rw, hwnd),
          "GetFocus=%s render=%s" % (u.GetFocus(), rw))

    # 切站点：复用同一个宿主，旧网页窗口关掉，不再多开一个
    n_before = len(L.tracked())
    _err = []
    # 全程盯着：新页面一旦"可见但还没对齐"，浏览器自己那条标题栏就会闪出来
    bad_frames = []

    def watch():
        for h in list(L.tracked().values()):
            if u.IsWindowVisible(ctypes.c_void_p(h)) \
                    and u.GetParent(ctypes.c_void_p(h)) == holder \
                    and not L.fit_ok(h, holder):
                bad_frames.append((h, L.content_rect(h), L.window_rect(holder)))

    webchat_ui.open_site_ui(SITES[1], finished=lambda ok, e: _err.append((ok, e)))
    dl = time.monotonic() + 40
    while time.monotonic() < dl and host._hwnd == hwnd:
        watch()
        pump(0.02)
    t_switch = time.monotonic()
    while time.monotonic() - t_switch < 2.0:
        watch()
        pump(0.02)
    check("切站点：全程没有出现「可见但没对齐」的一帧（不会闪出浏览器标题栏）",
          not bad_frames, "坏帧 %d 例：%s" % (len(bad_frames), bad_frames[:2]))
    check("切站点换了网页窗口", host._hwnd not in (None, hwnd),
          "host._hwnd=%s 旧=%s 打开结果=%s" % (host._hwnd, hwnd, _err))
    check("切站点后桌面上只看得见宿主一个窗口（旧页面藏起来了，不越开越多）",
          not any(u.IsWindowVisible(ctypes.c_void_p(h))
                  and L.owner_of(h) != int(host.winId())
                  for h in L.tracked().values())
          and not u.IsWindowVisible(ctypes.c_void_p(hwnd)),
          "tracked=%s 旧窗口可见=%s" % (list(L.tracked()),
                                        bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))))
    check("切站点后新页面也粘在宿主上",
          L.owner_of(host._hwnd) == int(host.winId()))
    # 切站点的一瞬间渲染子窗口可能还没建好（window_rect 会返回 None）。
    # 这正是让页面错位的那个瞬态：insets 量不到，以前会退回 (0,0,0,0)，
    # 于是不撑不裁、整页往下错、侧边栏被盖住。这里等它出来再断言。
    rw2, rr4, hr4 = None, None, None
    _dl = time.monotonic() + 5
    while time.monotonic() < _dl:
        rw2 = L._find_descendant(host._hwnd, "Chrome_RenderWidgetHostHWND")
        rr4 = L.window_rect(rw2) if rw2 else None
        hr4 = L.window_rect(holder)
        if rr4 and hr4:
            break
        pump(0.05)
    check("切完之后新网页也正好铺满容器",
          bool(rr4) and bool(hr4)
          and abs(rr4[2] - hr4[2]) <= 3 and abs(rr4[3] - hr4[3]) <= 3,
          "内容=%s 容器=%s" % (rr4, hr4))
    check("切站点后内缩量被记住了（下次量不到时才有好值可用）",
          L.last_insets(host._hwnd) is not None,
          "insets=%s" % (L.last_insets(host._hwnd),))
    # 模拟那个瞬态：让 insets 量不到，再摆一次 —— 页面必须还在原位，
    # 不能因为退回零内缩而整页错位
    _real_bi = L.browser_insets
    L.browser_insets = lambda h, timeout=0.0: None
    try:
        L.fit_browser(host._hwnd, holder)
        pump(0.15)
        rr5 = L.window_rect(
            L._find_descendant(host._hwnd, "Chrome_RenderWidgetHostHWND"))
        hr5 = L.window_rect(holder)
        check("内缩量一瞬间量不到时，页面仍然正好压住容器（不退回零内缩）",
              bool(rr5) and bool(hr5)
              and abs(rr5[0] - hr5[0]) <= 3 and abs(rr5[1] - hr5[1]) <= 3
              and abs(rr5[2] - hr5[2]) <= 3 and abs(rr5[3] - hr5[3]) <= 3,
              "内容=%s 容器=%s" % (rr5, hr5))
    finally:
        L.browser_insets = _real_bi
    check("旧页面还活着（切回去是瞬间的，状态不丢）",
          L.window_alive(hwnd) and not u.IsWindowVisible(ctypes.c_void_p(hwnd)))

    # 切回去：应当秒回，不再起浏览器
    t0 = time.monotonic()
    webchat_ui.open_site_ui(SITES[0])
    dl = time.monotonic() + 15
    while time.monotonic() < dl and host._hwnd != hwnd:
        pump(0.1)
    back = time.monotonic() - t0
    pump(1.0)
    check("切回上一个站点是瞬间的（复用已开的页面）",
          host._hwnd == hwnd and back < 1.5, "%.2fs" % back)
    check("切回后网页仍然铺满容器",
          abs(L.window_rect(L._find_descendant(hwnd, "Chrome_RenderWidgetHostHWND"))[2]
              - L.window_rect(holder)[2]) <= 3)

    # ---- 中文输入法：候选浮窗要跟着光标 ----
    # 两个前提，缺一个候选窗就飘：
    #   a) 活动窗口和键盘焦点必须落在**同一个线程**（浏览器那个）。SetParent 成
    #      宿主子窗口时活动窗口归桌宠线程、焦点归浏览器线程，Windows 的输入法 UI
    #      按活动窗口的线程走，够不到网页，于是退化成自己那套浮动候选窗（实测）。
    #   b) 浏览器认得自己的屏幕位置。顶层窗口天然一直是对的；子窗口那版因为
    #      "相对父窗口没位移" 收不到任何位置消息，一直停在出生时的 -32000。
    # 这里 b) 用网页里的 window.screenX/screenY 当探针（写进 document.title）。
    probe = os.path.join(profile, "pos_probe.html")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("<html><head><title>init</title></head><body style='background:#eef'>"
                "<input id=i style='margin:60px;font-size:20px' value='ime'>"
                "<script>function up(){document.title="
                "Math.round(window.screenX)+','+Math.round(window.screenY);}"
                "setInterval(up,100);up();"
                "document.getElementById('i').focus();</script></body></html>")
    PROBE = {"name": "定位探针", "url": "file:///" + probe.replace("\\", "/")}
    SITES.append(PROBE)
    webchat_ui.open_site_ui(PROBE)
    dl = time.monotonic() + 40
    while time.monotonic() < dl and host._hwnd in (None, hwnd):
        pump(0.1)
    ph = host._hwnd
    pump(3.5)          # 等页面加载完

    def believed():
        try:
            x, y = L.window_title(ph).split(",")
            return (int(x), int(y))
        except Exception:
            return None

    def real():
        r = L.window_rect(ph)
        return (r[0], r[1])

    check("输入法：打开页面后浏览器认得自己的屏幕位置（候选窗按它摆）",
          believed() is not None
          and abs(believed()[0] - real()[0]) <= 2
          and abs(believed()[1] - real()[1]) <= 2,
          "网页自报=%s 真实=%s" % (believed(), real()))
    host.move(560, 380)
    pump(0.6)
    check("输入法：宿主移动后位置依然是对的（顶层窗口自己知道）",
          believed() is not None
          and abs(believed()[0] - real()[0]) <= 2
          and abs(believed()[1] - real()[1]) <= 2,
          "网页自报=%s 真实=%s" % (believed(), real()))
    check("输入法：网页仍然正好压住容器（不露标题栏、不露黑边）",
          L.fit_ok(ph, holder) and L.owner_of(ph) == int(host.winId()))
    # 最关键的一条：活动窗口和键盘焦点必须同线程，否则输入法够不到网页
    L.focus_browser(ph)
    pump(0.6)
    g = _gui_thread_info()
    check("输入法：活动窗口和键盘焦点在同一个线程（候选窗才会跟着光标）",
          g is not None and g[0] and g[1] and _tid(g[0]) == _tid(g[1]),
          "active 线程=%s focus 线程=%s 页面线程=%s"
          % (_tid(g[0]) if g and g[0] else "-",
             _tid(g[1]) if g and g[1] else "-", _tid(ph)))
    check("输入法：活动窗口就是网页窗口本身（和独立浏览器一致）",
          g is not None and g[0] == ph, "active=%s 页面=%s" % (g[0] if g else None, ph))
finally:
    # 关窗口时盯着：解开 owner 之后页面一旦还看得见，就会露个面才消失
    left_top_flash = []
    pages = list(L.tracked().values())
    try:
        if host is not None:
            host.close()
            for _ in range(60):
                for h in pages:
                    if u.IsWindow(ctypes.c_void_p(h)) \
                            and u.IsWindowVisible(ctypes.c_void_p(h)) \
                            and L.owner_of(h):
                        left_top_flash.append((h, L.window_rect(h)))
                pump(0.02)
            check("关闭时页面没有脱开宿主还露着面（不会闪一下才消失）",
                  not left_top_flash,
                  "闪了 %d 帧：%s" % (len(left_top_flash), left_top_flash[:2]))
    except Exception as e:
        print("关闭检查出错:", e)
    pump(1.0)
    # 兜底：按"用了这个临时配置目录"精确定位进程，绝不 taskkill msedge.exe
    for pid in L._profile_pids():
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    time.sleep(1.0)
    L.profile_dir = _real_profile
    shutil.rmtree(profile, ignore_errors=True)
    print("临时配置目录已删除:", not os.path.exists(profile))

print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
