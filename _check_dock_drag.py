# -*- coding: utf-8 -*-
"""量一量：拖动聚合AI 窗口时，贴边栏到底有没有拖慢它。

对应用户提问："拖动聚合ai窗口明显感觉迟滞，是性能问题吗？"

做法是把"拖窗口"拆成可测的三组对照，每组连续移动父窗口 N 次，量**每次移动
花的时间**（SetWindowPos 的墙钟耗时）和**子窗口有没有跟丢**（相对偏移是否恒定）：
  A 不挂贴边栏              —— 基线，浏览器窗口自己能跑多快
  B 挂上、收起态（半透明 + 圆角裁剪）
  C 挂上、展开态（实心、无裁剪）
B/C 比 A 慢多少，就是我们这条栏的代价。

**临时 user-data-dir（跑完即删），只开 example.com，绝不碰 webchat_profile，
也绝不 taskkill msedge。**

用法：python _check_dock_drag.py
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
import webchat_ui as W                              # noqa: E402

u = ctypes.windll.user32
u.GetParent.restype = ctypes.c_void_p
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


SWP = 0x0001 | 0x0004 | 0x0010          # NOSIZE | NOZORDER | NOACTIVATE
STEPS = 60


def drag_run(parent, child):
    """来回挪 STEPS 次，返回 (每次耗时毫秒列表, 相对偏移集合)。"""
    times, offs = [], set()
    r0 = rect_of(parent)
    x0, y0 = r0[0], r0[1]
    for i in range(STEPS):
        dx = (i % 20) * 6 - 60
        dy = (i % 7) * 4
        t0 = time.perf_counter()
        u.SetWindowPos(ctypes.c_void_p(parent), None, x0 + dx, y0 + dy, 0, 0, SWP)
        app.processEvents()             # 真实场景里 Qt 的事件循环也在跑
        times.append((time.perf_counter() - t0) * 1000.0)
        if child:
            pr, cr = rect_of(parent), rect_of(child)
            if pr and cr:
                offs.add((cr[0] - pr[0], cr[1] - pr[1]))
        time.sleep(0.004)
    u.SetWindowPos(ctypes.c_void_p(parent), None, x0, y0, 0, 0, SWP)
    return times, offs


def stat(ts):
    s = sorted(ts)
    return (sum(ts) / len(ts), s[len(s) // 2], s[int(len(s) * 0.95)], max(ts))


exe, brand = L.browser_path()
if not exe:
    print("没找到 Edge / Chrome，跳过")
    sys.exit(0)
print("用", brand, "；每组移动 %d 次" % STEPS)

profile = tempfile.mkdtemp(prefix="oi_dockdrag_")
L.profile_dir = lambda: profile
L.save_sites = lambda s: None
L.set_last_site = lambda n: None
L.set_preferred_size = lambda w, h: None
SITE = {"name": "甲站", "url": "https://example.com"}
L.load_sites = lambda: [SITE]
W.dock_mode = lambda: True

hwnd = None
try:
    ok, err, hwnd = L.open_site(SITE, size=(900, 620))
    check("浏览器窗口开起来了", ok, err or "")
    if not ok:
        raise SystemExit(1)
    L.show_browser(hwnd)
    u.ShowWindow(ctypes.c_void_p(hwnd), 9)
    u.SetWindowPos(ctypes.c_void_p(hwnd), None, 360, 200, 900, 620, 0x0010)
    pump(2.0)
    if (rect_of(hwnd) or (-99999,))[0] < -10000:
        print("!! 窗口不在屏内，测量不可信")
        raise SystemExit(1)

    # ---- A：不挂贴边栏 ----
    a_t, _ = drag_run(hwnd, None)
    a = stat(a_t)
    print("A 不挂贴边栏        平均 %.2fms  中位 %.2fms  p95 %.2fms  最大 %.2fms" % a)

    # ---- B：挂上、收起态 ----
    W.attach_dock(hwnd)
    pump(1.2)
    d = W.dock()
    d._set_expanded(False)
    pump(0.5)
    ch = int(d.winId())
    b_t, b_offs = drag_run(hwnd, ch)
    b = stat(b_t)
    print("B 挂上·收起（半透明+圆角）平均 %.2fms  中位 %.2fms  p95 %.2fms  最大 %.2fms" % b)

    # ---- C：挂上、展开态 ----
    d._set_expanded(True)
    pump(0.6)
    c_t, c_offs = drag_run(hwnd, ch)
    c = stat(c_t)
    print("C 挂上·展开（实心）      平均 %.2fms  中位 %.2fms  p95 %.2fms  最大 %.2fms" % c)
    d._set_expanded(False)
    pump(0.4)

    # ---- D：对照组，挪一个我们自己的普通 Qt 窗口（和浏览器无关） ----
    # 用来回答"5ms 到底贵不贵、贵在谁身上"：如果普通窗口是 0.x ms，
    # 那基线那 5ms 就是 Chromium 自己挪窗口的代价，不是我们能优化的。
    from PyQt5.QtWidgets import QWidget
    plain = QWidget()
    plain.resize(900, 620)
    plain.show()
    pump(0.6)
    d_t, _ = drag_run(int(plain.winId()), None)
    dd_ = stat(d_t)
    print("D 普通 Qt 窗口（对照）   平均 %.2fms  中位 %.2fms  p95 %.2fms  最大 %.2fms"
          % dd_)
    plain.hide()
    plain.deleteLater()

    # ---------- 结论由数据驱动 ----------
    # 一帧 16.7ms。父窗口每挪一次我们额外吃掉多少，才是"卡不卡"的关键。
    over_b = b[0] - a[0]
    over_c = c[0] - a[0]
    print("\n   贴边栏带来的额外开销：收起 %+.2fms/次，展开 %+.2fms/次（一帧 16.7ms）"
          % (over_b, over_c))
    print("   同一套挪法，普通窗口 %.2fms vs 浏览器窗口 %.2fms —— 差的 %.2fms "
          "在浏览器身上" % (dd_[0], a[0], a[0] - dd_[0]))

    check("收起态：拖动时贴边栏的额外开销远小于一帧",
          over_b < 4.0, "额外 %+.2fms（基线 %.2fms → %.2fms）" % (over_b, a[0], b[0]))
    check("展开态：拖动时贴边栏的额外开销远小于一帧",
          over_c < 4.0, "额外 %+.2fms（基线 %.2fms → %.2fms）" % (over_c, a[0], c[0]))
    check("收起态：拖动全程子窗口相对偏移恒定（没跟丢、不会错位）",
          len(b_offs) == 1, "偏移集合 %s" % (sorted(b_offs)[:3],))
    check("展开态：拖动全程子窗口相对偏移恒定",
          len(c_offs) == 1, "偏移集合 %s" % (sorted(c_offs)[:3],))
    check("单次移动没有长尾卡顿（p95 在一帧以内）",
          b[2] < 16.7 and c[2] < 16.7,
          "收起 p95 %.2fms / 展开 p95 %.2fms" % (b[2], c[2]))

    # 同步定时器本身的代价：拖动时它每 120ms 会被叫醒一次
    t0 = time.perf_counter()
    for _ in range(50):
        d._sync()
    sync_ms = (time.perf_counter() - t0) / 50 * 1000.0
    check("同步定时器一拍很便宜（拖动时每 120ms 才跑一次）",
          sync_ms < 2.0, "一次 _sync = %.3fms" % sync_ms)
    check("对照：挪普通 Qt 窗口比挪浏览器窗口便宜得多（说明代价在浏览器那边）",
          dd_[0] < a[0], "普通 %.2fms vs 浏览器 %.2fms" % (dd_[0], a[0]))
finally:
    try:
        dd = W.dock(create=False)
        if dd is not None:
            dd.detach()
    except Exception:
        pass
    if hwnd:
        try:
            u.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)
        except Exception:
            pass
    pump(1.5)
    shutil.rmtree(profile, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
