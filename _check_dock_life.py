# -*- coding: utf-8 -*-
"""验真：贴边栏在「切站点」和「网页窗口被关掉」这两件事上的生存能力。

对应用户反馈：
  ① 切换其他模型会**弹出新页面**（旧窗口没藏起来，变成两个顶层窗口）；
  ② 都关掉之后再打开，**没有带侧边栏的窗口了**（怀疑：贴边栏是浏览器窗口的
     子窗口，Win32 规则是父窗口销毁时**连带销毁子窗口**，于是我们那个 Qt
     widget 的原生句柄被浏览器带走了，之后再也挂不上去）。

**临时 user-data-dir（跑完即删），只开 example.com / example.org，绝不碰
webchat_profile、不用任何已登录会话，也绝不 taskkill msedge。**

用法：python _check_dock_life.py
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


def visible(h):
    return bool(u.IsWindowVisible(ctypes.c_void_p(h)))


def alive(h):
    return bool(u.IsWindow(ctypes.c_void_p(h)))


exe, brand = L.browser_path()
if not exe:
    print("没找到 Edge / Chrome，跳过")
    sys.exit(0)
print("用", brand)

SITE_A = {"name": "甲站", "url": "https://example.com"}
SITE_B = {"name": "乙站", "url": "https://example.org"}
SITE_C = {"name": "丙站", "url": "https://example.net"}

profile = tempfile.mkdtemp(prefix="oi_docklife_")
L.profile_dir = lambda: profile
L.save_sites = lambda s: None
L.set_last_site = lambda n: None
L.set_preferred_size = lambda w, h: None
L.load_sites = lambda: [SITE_A, SITE_B, SITE_C]
# 贴边栏模式强制打开（别让本机 settings 把它关掉导致测了旧架构）
W.dock_mode = lambda: True

opened = []


def open_and_dock(site):
    ok, err, h = L.open_site(site, size=(820, 560))
    if not ok:
        return None, err
    opened.append(h)
    W.attach_dock(h)
    pump(1.0)
    return h, ""


try:
    # ---------- ① 切站点：应该只剩一个可见窗口，而且就地换内容 ----------
    h1, err = open_and_dock(SITE_A)
    check("第一个站点开起来并挂上了贴边栏", bool(h1), err)
    if not h1:
        raise SystemExit(1)
    u.ShowWindow(ctypes.c_void_p(h1), 9)             # SW_RESTORE
    u.SetWindowPos(ctypes.c_void_p(h1), None, 300, 160, 820, 560, 0x0010)
    pump(1.0)
    r1 = rect_of(h1)
    if not r1 or r1[0] < -10000:
        print("!! 窗口不在屏内，测量不可信")
        raise SystemExit(1)
    check("第一个窗口在屏幕上可见", visible(h1), "矩形 %s" % (r1,))

    d = W.dock()
    dh1 = int(d.winId())
    check("贴边栏的父窗口就是第一个网页窗口",
          u.GetParent(ctypes.c_void_p(dh1)) == h1)

    h2, err = open_and_dock(SITE_B)
    check("第二个站点开起来了", bool(h2), err)
    if h2:
        pump(1.2)
        check("切站点后**上一个窗口已经藏起来**（不该冒出第二个顶层窗口）",
              not visible(h1),
              "甲站 visible=%s / 乙站 visible=%s" % (visible(h1), visible(h2)))
        r2 = rect_of(h2)
        same = r2 and r1 and max(abs(r2[i] - r1[i]) for i in range(4)) <= 4
        check("切站点是**就地换内容**（新窗口摆在上一个窗口的位置和尺寸上）",
              bool(same), "甲站 %s → 乙站 %s" % (r1, r2))
        check("贴边栏跟到了新窗口上",
              u.GetParent(ctypes.c_void_p(int(d.winId()))) == h2)

        # 点"当前正在用的那个站点"不该让窗口跳一下
        r_before = rect_of(h2)
        W.attach_dock(h2)
        pump(0.8)
        r_after = rect_of(h2)
        check("再点一次当前站点，窗口**不动**（不该重新居中）",
              r_after == r_before, "%s -> %s" % (r_before, r_after))

    # ---------- ② 关掉网页窗口：贴边栏不能跟着死 ----------
    target = h2 or h1
    dh = int(d.winId())
    check("关之前，贴边栏的原生窗口是活的", alive(dh))
    WM_CLOSE = 0x0010
    for h in list(opened):
        u.PostMessageW(ctypes.c_void_p(h), WM_CLOSE, 0, 0)
    pump(2.5)
    child_died = not alive(dh)
    print("   （观察）父窗口关掉之后，贴边栏原生句柄还活着吗 ->",
          "已被系统连带销毁" if child_died else "还活着")

    # 关键验收：不管句柄死没死，再开一个站点必须**还能有带侧边栏的窗口**
    h3, err = open_and_dock(SITE_C)
    check("全关之后还能重新打开站点", bool(h3), err)
    if h3:
        d2 = W.dock()
        dh3 = int(d2.winId()) if d2 is not None else 0
        check("重新打开后**又有贴边栏了**（这正是用户说的「没有带侧边栏的主窗口」）",
              bool(d2) and alive(dh3)
              and u.GetParent(ctypes.c_void_p(dh3)) == h3,
              "dock=%s alive=%s parent=%s 期望 %s"
              % (bool(d2), alive(dh3) if dh3 else None,
                 u.GetParent(ctypes.c_void_p(dh3)) if dh3 else None, h3))
        check("重新打开的窗口是可见的", visible(h3))

    # ---------- ③ 收起态不该是「把全宽的栏硬裁一条」 ----------
    d3 = W.dock(create=False)
    if d3 is not None:
        d3._set_expanded(False)
        pump(0.6)
        win_w = d3.width()
        bar_w = d3.bar.width()
        bar_shown = d3.bar.isVisible()
        check("收起态：里面那条栏没有被窗口硬裁掉（要么收窄要么隐藏）",
              (not bar_shown) or bar_w <= win_w + 1,
              "窗口宽 %d / 栏宽 %d / 栏可见 %s" % (win_w, bar_w, bar_shown))
        check("收起态窄边够细（不挡页面），但也不是一条缝",
              d3._edge_w <= 28 and d3._edge_w >= 10, "窄边 %d px" % d3._edge_w)

        # ---------- ④ 滑出/滑回是动画，不是一帧跳过去 ----------
        widths = []
        d3._set_expanded(True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:
            app.processEvents()
            widths.append(int(round(d3._w)))
            time.sleep(0.008)
        uniq = sorted(set(widths))
        check("展开是滑出来的（中间量到多个过渡宽度，不是一帧跳到位）",
              len(uniq) >= 4 and uniq[0] <= d3._edge_w + 4
              and uniq[-1] >= d3._full_w - 1,
              "量到 %d 个不同宽度：%s…%s" % (len(uniq), uniq[:3], uniq[-2:]))
        check("展开到位后宽度正好是全宽", int(round(d3._w)) == d3._full_w,
              "%s / 全宽 %s" % (int(round(d3._w)), d3._full_w))
        check("展开态里面那条栏左缘对齐在 0（没有横向错位）",
              d3.bar.x() == 0, "bar.x = %d" % d3.bar.x())

        widths = []
        d3._set_expanded(False)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:
            app.processEvents()
            widths.append(int(round(d3._w)))
            time.sleep(0.008)
        uniq = sorted(set(widths))
        check("收回也是滑回去的（不是一帧跳回）",
              len(uniq) >= 4 and uniq[-1] >= d3._full_w - 4
              and uniq[0] <= d3._edge_w + 1,
              "量到 %d 个不同宽度" % len(uniq))
        check("收回到位后栏已隐藏（窄边上画把手，不露半个按钮）",
              not d3.bar.isVisible() and int(round(d3._w)) == d3._edge_w)

        # ---------- ⑤ 收起态要少挡页面 + 半透明 ----------
        d3._set_expanded(False)
        pump(0.5)
        pr = rect_of(d3._hwnd) if d3._hwnd else None
        dr = rect_of(int(d3.winId()))
        if pr and dr:
            cover = (dr[2] * dr[3]) / float(max(1, pr[2] * pr[3]))
            check("收起态遮住页面的面积很小（不再贴满整条左缘）",
                  cover < 0.04,
                  "把手 %dx%d / 窗口 %dx%d = %.1f%%"
                  % (dr[2], dr[3], pr[2], pr[3], cover * 100))
            check("收起态只占左缘中间一小段高度，不是满高",
                  dr[3] <= pr[3] * 0.4,
                  "把手高 %d / 窗口高 %d" % (dr[3], pr[3]))
            check("收起态纵向居中（在左缘中间，位置可预期好找）",
                  abs((dr[1] + dr[3] / 2) - (pr[1] + pr[3] / 2)) <= 4,
                  "把手中心 y=%d / 窗口中心 y=%d"
                  % (dr[1] + dr[3] // 2, pr[1] + pr[3] // 2))

        # 半透明是不是真的生效了（WS_EX_LAYERED + 实际 alpha 值）
        dh_now = int(d3.winId())
        ex = u.GetWindowLongW(ctypes.c_void_p(dh_now), -20) & 0xFFFFFFFF
        alpha = ctypes.c_ubyte(0)
        flags = ctypes.c_ulong(0)
        got = u.GetLayeredWindowAttributes(ctypes.c_void_p(dh_now), None,
                                          ctypes.byref(alpha),
                                          ctypes.byref(flags))
        check("收起态半透明真的生效了（WS_EX_LAYERED 已置上）",
              bool(ex & 0x00080000), "exstyle=0x%08X" % ex)
        check("收起态不透明度约 85%（= 用户要的 15% 透明）",
              bool(got) and 200 <= alpha.value <= 230,
              "alpha=%d（255 为实心）" % alpha.value)

        d3._set_expanded(True)
        pump(0.5)
        dh_exp = int(d3.winId())
        alpha2 = ctypes.c_ubyte(0)
        got2 = u.GetLayeredWindowAttributes(ctypes.c_void_p(dh_exp), None,
                                            ctypes.byref(alpha2),
                                            ctypes.byref(flags))
        ex2 = u.GetWindowLongW(ctypes.c_void_p(dh_exp), -20) & 0xFFFFFFFF
        # 不透明度回到 1.0 时 Qt 会把 WS_EX_LAYERED **整个摘掉** —— 没有 layered
        # 位和 alpha=255 都表示"实心"，两者都算过。
        check("展开后变回实心（按钮文字不该是半透的）",
              (not ex2 & 0x00080000) or (got2 and alpha2.value == 255),
              "exstyle=0x%08X got=%s alpha=%d" % (ex2, bool(got2), alpha2.value))
        d3._set_expanded(False)
        pump(0.4)
finally:
    try:
        d = W.dock(create=False)
        if d is not None:
            d.detach()
    except Exception:
        pass
    WM_CLOSE = 0x0010
    for h in list(opened):
        try:
            u.PostMessageW(ctypes.c_void_p(h), WM_CLOSE, 0, 0)
        except Exception:
            pass
    pump(1.5)
    shutil.rmtree(profile, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
