# -*- coding: utf-8 -*-
"""验真：贴边栏（DockBar）挂进真实 Edge --app 窗口，位置/尺寸/命中/重挂都要对。

这是"方案一"的验收脚本。**临时 user-data-dir（跑完即删），只开 example.com，
绝不碰 webchat_profile、不用任何已登录会话。**

用法：python _check_dock.py
"""
import ctypes
import os
import shutil
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication            # noqa: E402
app = QApplication([])

import webchat_launcher as L                        # noqa: E402
from webchat_dock import DockBar                    # noqa: E402

u = ctypes.windll.user32
for fn in ("GetParent", "WindowFromPoint"):
    getattr(u, fn).restype = ctypes.c_void_p
u.GetParent.argtypes = [ctypes.c_void_p]

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def pump(sec):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        app.processEvents()
        time.sleep(0.01)


def rect_of(h):
    buf = ctypes.create_string_buffer(16)
    if not u.GetWindowRect(ctypes.c_void_p(h), buf):
        return None
    l, t, r, b = struct.unpack("4i", buf.raw)
    return (l, t, r - l, b - t)


def cls_of(h):
    buf = ctypes.create_unicode_buffer(160)
    u.GetClassNameW(ctypes.c_void_p(h), buf, 160)
    return buf.value


exe, brand = L.browser_path()
if not exe:
    print("没找到 Edge / Chrome，跳过")
    sys.exit(0)
print("用", brand)

profile = tempfile.mkdtemp(prefix="oi_dock_check_")
_real = L.profile_dir
L.profile_dir = lambda: profile
L.save_sites = lambda s: None
L.set_last_site = lambda n: None
L.set_preferred_size = lambda w, h: None
L.load_sites = lambda: [{"name": "甲站", "url": "https://example.com"},
                        {"name": "乙站", "url": "https://example.org"}]

dock = None
try:
    ok, err, hwnd = L.open_site({"name": "甲站", "url": "https://example.com"},
                                size=(900, 640))
    check("浏览器窗口开起来了", ok, err or "")
    if not ok:
        raise SystemExit(1)
    L.show_browser(hwnd)
    u.ShowWindow(ctypes.c_void_p(hwnd), 9)           # SW_RESTORE
    u.SetWindowPos(ctypes.c_void_p(hwnd), None, 320, 180, 900, 640, 0x0010)
    pump(1.5)
    pr = rect_of(hwnd)
    if not pr or pr[0] < -10000:
        print("!! 窗口不在屏内，测量不可信")
        raise SystemExit(1)

    dock = DockBar()
    check("贴边栏挂进了浏览器窗口（成为它的子窗口）", dock.attach_to(hwnd))
    pump(0.6)
    dh = int(dock.winId())
    check("父窗口确实是浏览器窗口",
          u.GetParent(ctypes.c_void_p(dh)) == hwnd)

    # z 序：必须在渲染窗口之上，否则会被页面盖住
    kids = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(h, _):
        kids.append(h)
        return True

    u.EnumChildWindows(ctypes.c_void_p(hwnd), _cb, 0)
    idx_dock = kids.index(dh) if dh in kids else -1
    idx_rw = next((i for i, k in enumerate(kids)
                   if cls_of(k).startswith("Chrome_RenderWidgetHostHWND")), -1)
    check("z 序在网页渲染窗口之上（不会被页面盖住）",
          idx_dock >= 0 and (idx_rw < 0 or idx_dock < idx_rw),
          "贴边栏第 %d 个子窗口，渲染窗口第 %d 个" % (idx_dock, idx_rw))

    # ---- 位置由系统跟随：移动父窗口，相对偏移必须恒定 ----
    offs = []
    for dx, dy in ((90, 60), (-150, 40), (70, -80)):
        p0 = rect_of(hwnd)
        u.SetWindowPos(ctypes.c_void_p(hwnd), None,
                       p0[0] + dx, p0[1] + dy, 0, 0, 0x0001 | 0x0004 | 0x0010)
        pump(0.15)
        p1, d1 = rect_of(hwnd), rect_of(dh)
        offs.append((d1[0] - p1[0], d1[1] - p1[1]))
    check("移动父窗口：相对偏移恒定（系统替我们跟随，零摆位代码）",
          len(set(offs)) == 1, "偏移序列 %s" % (offs,))

    # ---- 尺寸要我们自己跟（已知不自动）：缩放父窗口后高度必须对上 ----
    bad_h = []
    for w, h in ((780, 520), (1060, 720), (900, 600)):
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, w, h,
                       0x0002 | 0x0004 | 0x0010)
        pump(0.5)                                    # 等同步定时器跑一拍
        p1, d1 = rect_of(hwnd), rect_of(dh)
        if abs(d1[3] - p1[3]) > 2:
            bad_h.append((h, d1[3], p1[3]))
    check("缩放父窗口：贴边栏高度跟着变（这条系统不管，我们自己同步）",
          not bad_h, "没跟上的：%s" % (bad_h[:3],))

    # ---- 自动隐藏：收起态只占一条窄边，展开后是全宽 ----
    dock._set_expanded(False)
    pump(0.3)
    w_collapsed = rect_of(dh)[2]
    dock._set_expanded(True)
    pump(0.3)
    w_expanded = rect_of(dh)[2]
    check("自动隐藏：收起只占一条窄边，展开才全宽（%d -> %d px）"
          % (w_collapsed, w_expanded),
          w_collapsed < w_expanded
          and w_collapsed <= dock._edge_w + 2)

    # ---- 鼠标命中：沿栏竖着打点，至少大部分要落在我们身上 ----
    dock._set_expanded(True)
    pump(0.3)
    dr = rect_of(dh)
    hits = 0
    pts = [(dr[0] + dr[2] // 2, dr[1] + int(dr[3] * f))
           for f in (0.3, 0.5, 0.7, 0.9)]
    for px, py in pts:
        pt = ctypes.c_longlong((py << 32) | (px & 0xFFFFFFFF))
        g = u.WindowFromPoint(pt)
        hits += 1 if g == dh else 0
    check("鼠标命中：跨进程子窗口能收到鼠标（%d/%d 个点）" % (hits, len(pts)),
          hits >= len(pts) - 1)

    # ---- 挂接掉了能自己补回来（Edge 重建窗口时） ----
    u.SetParent(ctypes.c_void_p(dh), None)
    pump(0.05)
    check("先人为解开挂接", u.GetParent(ctypes.c_void_p(dh)) != hwnd)
    pump(0.6)                                        # 等同步定时器发现并补挂
    check("挂接掉了会自动补回来（Edge 重建窗口也不怕）",
          u.GetParent(ctypes.c_void_p(dh)) == hwnd)


    # ---- 走完整应用路径：open_site_ui + 切站点，都要挂好 ----
    print("")
    print("--- 走应用真实路径（open_site_ui / 切站点）---")
    import webchat_ui
    import pet_gravity as _PG
    _real_load = _PG.load_settings
    _PG.load_settings = lambda: {"webchat_dock": True}
    try:
        check("贴边栏模式已启用", webchat_ui.dock_mode())
        # 先把刚才那个窗口和栏收掉，重新走一遍应用入口
        dock.detach()
        webchat_ui._DOCK = None
        for pid in L._profile_pids():
            try:
                os.kill(pid, 9)
            except Exception:
                pass
        pump(1.2)
        webchat_ui.open_site_ui({"name": "甲站", "url": "https://example.com"})
        dl = time.monotonic() + 30
        d2 = None
        while time.monotonic() < dl:
            pump(0.1)
            d2 = webchat_ui.dock(create=False)
            if d2 is not None and d2._hwnd:
                break
        check("open_site_ui：网页窗口开出来并挂上了站点栏",
              d2 is not None and bool(d2._hwnd))
        if d2 is not None and d2._hwnd:
            pump(1.0)
            dh2 = int(d2.winId())
            check("应用路径：父窗口就是网页窗口（没有多余的宿主窗口）",
                  u.GetParent(ctypes.c_void_p(dh2)) == d2._hwnd)
            check("应用路径：网页窗口是可见的",
                  bool(u.IsWindowVisible(ctypes.c_void_p(d2._hwnd))))
            check("应用路径：没有创建旧架构的宿主窗口",
                  webchat_ui.host(create=False) is None)
            p2, dr2 = rect_of(d2._hwnd), rect_of(dh2)
            check("应用路径：网页窗口被摆到屏内（不是停在 -32000 的隐藏坐标）",
                  p2[0] > -10000 and p2[1] > -10000, "窗口=%s" % (p2,))
            check("应用路径：栏贴在网页窗口左边、高度一致",
                  dr2[0] == p2[0] and abs(dr2[3] - p2[3]) <= 2,
                  "栏=%s 窗口=%s" % (dr2, p2))
    finally:
        _PG.load_settings = _real_load
        dock = webchat_ui.dock(create=False) or dock

finally:
    try:
        if dock is not None:
            dock.detach()
    except Exception:
        pass
    app.processEvents()
    for pid in L._profile_pids():
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    time.sleep(1.0)
    L.profile_dir = _real
    shutil.rmtree(profile, ignore_errors=True)
    print("临时配置目录已删除:", not os.path.exists(profile))

print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
