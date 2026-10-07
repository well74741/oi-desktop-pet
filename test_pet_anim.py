# -*- coding: utf-8 -*-
"""桌宠倾角动画 + 设置窗悬停提示的回归测试。

倾角部分回归的是两个真实 bug：
1) 拖动开始时代码直接把倾角写 0，下一帧角度就从 25° 掉成 0°——一帧切；
2) 吸附时长只有 0.1s，且用 ease_out_back（30% 的时间走完 90% 的角度），
   60fps 下单帧要转 15°，同样看不出运动。
所以这里量的是**每帧转多少度**，不是"有没有动画"。

提示部分回归的是「模块列表」标题同时弹两个 tooltip（一个黄底一个深色）。

运行：python test_pet_anim.py（离屏；第 9/11 节会真的造 GravityPet，所以
**用户数据先隔离到临时沙箱**，见下方）。
"""
import json
import math
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 隔离用户数据 ----
# 这里会造真的 GravityPet，构造时会 load_settings()。以前没隔离，读的是开发者
# **本机真实的 pet_settings.json**：本地设的是动图所以跑 52 项，全新克隆里没有
# 那份设置、默认是静态图，第 11 节 4 条动图断言就被静默跳过（49 项）——
# 同一份代码在不同机器上测的东西不一样，而且离"写坏真实设置"只差一行。
import data_store                                   # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_petanim_")
data_store.DATA_DIR = SAND
# 显式指定多帧动图，第 11 节的播放断言在哪台机器上都会真的跑
with open(os.path.join(SAND, "pet_settings.json"), "w", encoding="utf-8") as _f:
    json.dump({"pet_image": "assets/yxm.webp"}, _f)

from PyQt5.QtCore import QElapsedTimer               # noqa: E402
from PyQt5.QtWidgets import QApplication             # noqa: E402

app = QApplication([])

import pet_gravity as G                              # noqa: E402

_cfg = G.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    # 宁可不测，也绝不碰用户真实设置
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

PASS, FAIL = [], []
HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


class Rotor(object):
    """只带倾角状态的替身：把 GravityPet 的两个方法绑上来驱动。

    桌宠本体要托盘、定时器、气泡一整套，测倾角用不着；补间逻辑本身是
    自包含的（只读写下面这几个字段）。
    """

    def __init__(self):
        self._current_rotation = 0.0
        self._rest_rotation = 0.0
        self._snap_rotation = 0.0
        self._rot_from = 0.0
        self._rot_to = 0.0
        self._rot_dur = 0.0
        self._rot_overshoot = True
        self._rot_active = False
        self._rot_timer = QElapsedTimer()

    start = G.GravityPet._start_rot_anim
    step = G.GravityPet._step_rotation


def curve(ease, duration, span=G.EDGE_TILT_ANGLE, fps_ms=G.FRAME_MS_BUSY):
    """按理想 60fps 采样一条缓动曲线，返回每帧角度。

    用解析采样而不是真 sleep：真 sleep 的抖动会让"每帧转多少度"这个指标
    忽大忽小，测出来的是机器负载，不是缓动曲线。
    """
    n = max(1, int(round(duration * 1000.0 / fps_ms)))
    return [ease(i / float(n)) * span for i in range(n + 1)]


def steps(angles):
    return [abs(angles[i + 1] - angles[i]) for i in range(len(angles) - 1)]


# ---------- 1. 缓动曲线：每帧只能转一点点，且要转很多帧 ----------
for tag, dur in (("吸附倾倒", G.SNAP_ROT_DURATION),
                 ("拔离回正", G.UNSNAP_ROT_DURATION)):
    ang = curve(G.ease_tilt, dur)
    d = steps(ang)
    moving = sum(1 for x in d if x > 0.2)
    check("%s：单帧最多转 %.2f°（不超过 4°才谈得上过渡）" % (tag, max(d)),
          max(d) <= 4.0, "%.2fs / %d 帧" % (dur, len(d)))
    check("%s：至少 10 帧在动（看得见的运动，又不拖沓）" % tag,
          moving >= 10, "在动的帧 %d / 共 %d" % (moving, len(d)))
    check("%s：末尾精确落在目标角度" % tag, abs(ang[-1] - G.EDGE_TILT_ANGLE) < 1e-9)
    check("%s：末段有一点过冲再落回（有弹性，但别过头）" % tag,
          0.3 < max(ang) - G.EDGE_TILT_ANGLE < 3.0,
          "过冲 %.2f°" % (max(ang) - G.EDGE_TILT_ANGLE))

# 回归：旧的 ease_out_back + 0.1s 就是用户看到的"一帧切"，留在这儿做对照
old = steps(curve(G.ease_out_back, 0.10))
check("对照：旧曲线单帧要转 %.1f°（这就是被吐槽的一帧切）" % max(old),
      max(old) > 10.0)
check("时长：够快，倾倒不超过 0.25s（用户要求比上一版快一倍）",
      G.SNAP_ROT_DURATION <= 0.25 and G.UNSNAP_ROT_DURATION <= 0.25,
      "倾倒 %.2fs / 回正 %.2fs" % (G.SNAP_ROT_DURATION, G.UNSNAP_ROT_DURATION))
check("时长：倾角比位移长（先滑到边上、再倒过去）",
      G.SNAP_ROT_DURATION > G.SNAP_MOVE_DURATION,
      "旋转 %.2fs / 位移 %.2fs" % (G.SNAP_ROT_DURATION, G.SNAP_MOVE_DURATION))

# ---------- 2. 吸附：0° → 25°，第一帧不许到位 ----------
r = Rotor()
r._snap_rotation = G.EDGE_TILT_ANGLE
r.start(G.EDGE_TILT_ANGLE, G.SNAP_ROT_DURATION)
check("吸附：补间被激活", r._rot_active is True)
check("吸附：第一帧几乎没动（不是一帧切）",
      abs(r.step(0.0)) < 2.0, "第一帧 %.2f°" % r._rest_rotation)
time.sleep(G.SNAP_ROT_DURATION * 0.5)
half = r.step(0.0)
check("吸附：半程时在中间某个角度上", 3.0 < abs(half) < G.EDGE_TILT_ANGLE - 3.0,
      "半程 %.2f°" % half)
check("吸附：半程时补间还在跑（没提前结束）", r._rot_active is True)
time.sleep(G.SNAP_ROT_DURATION * 0.6)
r.step(0.0)
check("吸附：结束时精确落在目标角度，且补间自己停掉",
      abs(r._rest_rotation - G.EDGE_TILT_ANGLE) < 1e-9
      and r._rot_active is False, "%.6f°" % r._rest_rotation)

# ---------- 3. 拔离：25° → 0°，第一帧还得是倾斜的 ----------
r = Rotor()
r._rest_rotation = r._snap_rotation = G.EDGE_TILT_ANGLE
r.start(0.0, G.UNSNAP_ROT_DURATION)
r._snap_rotation = 0.0          # 和 mouseMoveEvent 里的顺序一致
check("拔离：第一帧还保持着倾斜（不是一帧归零）",
      abs(r.step(0.0) - G.EDGE_TILT_ANGLE) < 2.0,
      "第一帧 %.2f°" % r._rest_rotation)
time.sleep(G.UNSNAP_ROT_DURATION * 0.5)
half = r.step(0.0)
check("拔离：半程时立到一半", 3.0 < abs(half) < G.EDGE_TILT_ANGLE - 3.0,
      "半程 %.2f°" % half)
time.sleep(G.UNSNAP_ROT_DURATION * 0.6)
r.step(0.0)
check("拔离：结束时归零且补间停掉",
      abs(r._rest_rotation) < 1e-9 and r._rot_active is False)

# ---------- 4. 关掉过冲时不许越过目标 ----------
no_over = curve(G.smoothstep, 0.3, span=10.0)
check("过冲：关掉时曲线单调不越界（ease=smoothstep）",
      max(no_over) <= 10.0 + 1e-9
      and all(no_over[i + 1] >= no_over[i] for i in range(len(no_over) - 1)))

# ---------- 5. 静止时跟着 _snap_rotation，呼吸叠在上面 ----------
r = Rotor()
r._snap_rotation = -G.EDGE_TILT_ANGLE
check("静止：没有补间时倾角就是吸附角",
      abs(r.step(0.0) + G.EDGE_TILT_ANGLE) < 1e-9)
check("静止：呼吸叠加在吸附角之上（贴边也还在呼吸）",
      abs(r.step(1.5) - (-G.EDGE_TILT_ANGLE + 1.5)) < 1e-9)
r = Rotor()
r._rest_rotation = 10.0
r._snap_rotation = 10.0
r.start(0.0, G.UNSNAP_ROT_DURATION)
check("补间期间呼吸也照叠（结束那帧不会因为突然加呼吸而跳一下）",
      abs(r.step(1.5) - (r._rest_rotation + 1.5)) < 1e-9)

# ---------- 6. 目标没变化时不空跑一段补间 ----------
r = Rotor()
r._rest_rotation = 0.0
r.start(0.0, G.SNAP_ROT_DURATION)
check("同角度：目标和当前一致就不开补间", r._rot_active is False)

# ---------- 7. 位移完成判定：必须用未缓动的进度 ----------
# ease_out_back 中途会冲过 1.0，拿缓动值判 >=1 会让吸附位移在 60% 处就停在
# 过冲的位置上（桌宠停在比目标更靠外的地方）。
peak = max(G.ease_out_back(t / 100.0) for t in range(101))
check("位移：ease_out_back 中途确实会超过 1.0（所以只能用原始进度判完成）",
      peak > 1.0, "峰值 %.3f" % peak)
src = open(os.path.join(HERE, "pet_gravity.py"), encoding="utf-8").read()
pet = src[src.index("class GravityPet"):]
snap_blk = pet[pet.index("if self.anim_type == \"snap\":"):]
snap_blk = snap_blk[:snap_blk.index("_step_rotation")]
check("位移：吸附位移拿原始进度 raw 判完成，不是拿缓动值",
      "if raw >= 1.0:" in snap_blk and "if t_snap >= 1.0" not in snap_blk)

# ---------- 8. 倾角只走补间这一条路 ----------
body = pet[pet.index("def mouseMoveEvent"):pet.index("def contextMenuEvent")]
check("回归：拖拽/松手里不再直接写 _current_rotation（那就是一帧切）",
      "_current_rotation =" not in body)
check("回归：拖拽开始处调的是补间", "_start_rot_anim(0.0" in body)
check("回归：_snap_rotation 归零之前先起了回正补间",
      body.index("_start_rot_anim(0.0") < body.index("self._snap_rotation = 0.0"))
check("回归：倾角补间也算「忙」，帧率不会掉到 30fps",
      "_rot_active" in pet[pet.index("def _frame_is_busy"):
                           pet.index("def _request_paint")])

# ---------- 9. 模块列表标题：悬停只弹一个提示，而且是深色那个 ----------
# 以前标题同时挂了 setToolTip（Qt 延迟原生提示）和 eventFilter 里的
# QToolTip.showText（即时），先后各弹一个；而 showText 不传控件时用的是系统
# 调色板 ToolTipBase(#ffffdc)，就是用户看到的那块黄底。
from PyQt5.QtCore import QEvent                                  # noqa: E402
from PyQt5.QtWidgets import QToolTip                              # noqa: E402

dlg = G.SettingsDialog({})       # 空配置：绝不读写用户的 pet_settings.json
title = dlg._rules_title_label
check("提示：标题不再挂原生 tooltip（否则会再弹第二个）",
      title.toolTip() == "", "toolTip=%r" % title.toolTip())
_shown = []
_real_show = QToolTip.showText
QToolTip.showText = staticmethod(lambda *a, **k: _shown.append(a))
try:
    dlg.eventFilter(title, QEvent(QEvent.Enter))
finally:
    QToolTip.showText = _real_show
check("提示：悬停时只弹一个，且弹的是自己那条即时提示", len(_shown) == 1)
check("提示：showText 传了控件（才能吃到设置窗的深色 QToolTip 样式，不是黄底）",
      bool(_shown) and len(_shown[0]) >= 3 and _shown[0][2] is title,
      "参数 %d 个" % (len(_shown[0]) if _shown else 0))
qss = dlg.styleSheet()
# 提示框样式**只准有一处定义**：kit.TOOLTIP_QSS，由 main.py 挂到 QApplication。
# 以前设置窗自己一套（浅色经 _DARK_SUBS 变深）、画布一套浅色、气泡里的模块行
# 干脆没样式（吃系统调色板 #ffffdc 黄底）——同一个气泡里悬停标题和悬停按钮
# 会弹出两种长相，这就是用户报的"两种风格的标签"。
from widgets import kit as _kit_tip                             # noqa: E402

check("提示：全局只有一处定义（kit.TOOLTIP_QSS）",
      "QToolTip" in _kit_tip.TOOLTIP_QSS and "#232a3a" in _kit_tip.TOOLTIP_QSS)
check("提示：设置窗不再自带 QToolTip 规则（否则会盖掉全局那条）",
      "QToolTip" not in qss)
_tip_srcs = []
for _f in ("pet_gravity.py", "bubble_ui.py", "bubble_layout.py",
           "widgets/canvas.py", "widgets/perler.py", "widgets/stats.py",
           "widgets/tokenmeter.py", "widgets/todo.py", "widgets/notes.py"):
    try:
        _txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), _f),
                    encoding="utf-8").read()
    except Exception:
        continue
    if "QToolTip{" in _txt or "QToolTip {" in _txt:
        # 只算真正的样式表字符串，注释里提一嘴不算
        if any(('"' in _ln or "'" in _ln) and "QToolTip" in _ln
               and not _ln.strip().startswith("#")
               and "`QToolTip" not in _ln
               for _ln in _txt.splitlines()):
            _tip_srcs.append(_f)
check("提示：没有别的文件再写自己的 QToolTip 规则", not _tip_srcs,
      "还在写的：" + "、".join(_tip_srcs))
dlg.deleteLater()

# ---------- 10. 空菜单盘：开合动画要和有按钮时一样收得干净 ----------
# 回归：完成判定原先看 `_sector_scale >= 0.999`，而 ease_out_back 中途会冲到
# 1.045；空盘没有按钮陪跑（all_done 恒为 True），于是在过冲峰值上就收工、
# 定时器停掉，盘子永久停在比最终尺寸大 4.5% 的地方——这就是"看着生硬"。
from PyQt5.QtGui import QFont, QFontMetrics                        # noqa: E402

pet_w = G.GravityPet({})
pet_w.show()
app.processEvents()
menu = pet_w.radial_menu


def run_menu(shortcuts, seconds=0.75):
    menu.show_menu(shortcuts)
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        time.sleep(0.016)
    opened = (round(menu._sector_scale, 4), menu._animating)
    menu.hide_menu()
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        time.sleep(0.016)
    return opened, (round(menu._sector_scale, 4), menu._animating,
                    menu.isVisible())


SC = [{"name": "a", "path": r"C:\Windows\notepad.exe"}] * 4
for tag, sc in (("空盘", []), ("有按钮", SC)):
    op, cl = run_menu(sc)
    check("%s：展开到位后盘面精确停在 1.0（不卡在过冲上）" % tag,
          op == (1.0, False), "scale=%s animating=%s" % op)
    check("%s：收起后盘面精确归零且窗口收掉" % tag,
          cl == (0.0, False, False), "scale=%s animating=%s visible=%s" % cl)

# ---------- 11. 全屏下热键：桌宠要和菜单一起出来，收回后再自己藏好 ----------
# 回归：全屏看视频时 _check_fullscreen 把桌宠藏了，而 _on_hotkey 没管这个状态，
# 照样展开菜单 —— 菜单是独立顶层窗口，于是屏幕上只剩一个孤零零的盘子。
# 桌宠不可见就点不到它来收回，"点盘外"又等不到（那个窗口不该抓焦点），
# 菜单于是关不掉。用户原话："会出现一个单独的菜单盘，桌宠消失了，并且无法关闭"。
pet_w.fs_timer.stop()          # 别让 500ms 轮询插进来改 is_fullscreen
pet_w.is_fullscreen = True


def _hotkey_summon():
    pet_w.hide()
    app.processEvents()
    pet_w._on_hotkey()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.9:
        app.processEvents()
        time.sleep(0.016)
    return pet_w.isVisible(), menu.isVisible()


_vis, _mvis = _hotkey_summon()
check("全屏热键：桌宠和菜单一起出来（不再只剩一个孤盘）",
      _vis and _mvis, "桌宠=%s 菜单=%s" % (_vis, _mvis))
check("全屏热键：展开期间桌宠不会又被全屏轮询藏回去",
      pet_w.isVisible() and pet_w.is_fullscreen)

# 收回路径不止一条，每条都要把桌宠送回隐藏状态
for tag, close in (
        ("立即收（点盘外 / animate=False）",
         lambda: menu.hide_menu(animate=False)),
        ("动画播完（点按钮启动程序）", lambda: menu.hide_menu()),
        ("点桌宠收回", lambda: menu.toggle_menu(SC))):
    _vis, _mvis = _hotkey_summon()
    assert _vis and _mvis, "前置条件：%s 之前应当是展开的" % tag
    close()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.0 and (menu.isVisible() or menu._animating):
        app.processEvents()
        time.sleep(0.016)
    check("全屏热键·%s：菜单收起了" % tag, not menu.isVisible())
    check("全屏热键·%s：桌宠跟着藏回去（不然它会一直压在全屏视频上）" % tag,
          not pet_w.isVisible(), "桌宠可见=%s" % pet_w.isVisible())
    check("全屏热键·%s：临时叫醒的标志清掉了" % tag,
          pet_w._fs_summoned is False)

# 非全屏：热键呼出后收起，桌宠必须还在（别把正常情况也藏了）
pet_w.is_fullscreen = False
pet_w._fs_summoned = False
pet_w.show()
pet_w._on_hotkey()
t0 = time.monotonic()
while time.monotonic() - t0 < 0.9:
    app.processEvents()
    time.sleep(0.016)
menu.hide_menu(animate=False)
t0 = time.monotonic()
while time.monotonic() - t0 < 0.5:
    app.processEvents()
    time.sleep(0.016)
check("非全屏：热键呼出再收起，桌宠照旧留在屏幕上",
      pet_w.isVisible() and not menu.isVisible())
pet_w.fs_timer.start(500)

# 提示文字排版：屏内 / 不被桌宠压住 / 不超出盘沿，放不下就竖排
_f = QFont("Microsoft YaHei")
# 字号要和真实绘制一致（pet_gravity 里是 13.5 * pet_k()）：13.5 = 旧版的 9 × 1.5，
# v0.9.42 把那一次 1.5 烘进了源码。这里若还写 9，量出来的字比实际小一圈，
# "窄带里放不下就竖排"这条根本触发不了。
_f.setPointSizeF(13.5 * G._kit.pet_k())
_fm = QFontMetrics(_f)
scr = G._virtual_geo()
SPOTS = [("居中", scr.center().x(), scr.center().y()),
         ("吸附上", scr.center().x(), scr.top() - 30),
         ("吸附左", scr.left() - 30, scr.center().y()),
         ("吸附右", scr.right() + 30, scr.center().y()),
         ("吸附下", scr.center().x(), scr.bottom() + 30)]
# 桌宠摆到屏幕外的坐标上量，别在用户桌面中央闪一下
bad_scr, bad_pet, bad_disk, vertical = [], [], [], []
off_axis = []
for tag, gx, gy in SPOTS:
    pet_w.move(gx - pet_w.width() // 2, gy - pet_w.height() // 2)
    app.processEvents()
    menu.show_menu([])
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.55:
        app.processEvents()
        time.sleep(0.016)
    full = menu._sector_outer_full()
    lines, lw, lh, tx, ty = menu._empty_hint_layout(full, _fm)
    cx, cy = menu._center_pos.x(), menu._center_pos.y()
    bh = lh * len(lines)
    x, y = tx - lw / 2.0, ty - bh / 2.0
    far = max(math.hypot(qx - cx, qy - cy) for qx, qy in
              ((x, y), (x + lw, y), (x, y + bh), (x + lw, y + bh)))
    ph = pet_w.pet_size * 0.5 + 2
    org = menu.mapToGlobal(G.QPoint(0, 0))
    if not (scr.left() <= org.x() + x and org.x() + x + lw <= scr.right()
            and scr.top() <= org.y() + y and org.y() + y + bh <= scr.bottom()):
        bad_scr.append(tag)
    if not (x + lw < cx - ph or x > cx + ph
            or y + bh < cy - ph or y > cy + ph):
        bad_pet.append(tag)
    if far > full - 3:
        bad_disk.append((tag, round(far - full)))
    if len(lines) > 1:
        vertical.append(tag)
    # 必须落在桌宠的正中轴线上：正下 / 正上 / 正右 / 正左，不许歪着
    if min(abs(tx - cx), abs(ty - cy)) > 1.0:
        off_axis.append((tag, round(tx - cx), round(ty - cy)))
    menu.hide_menu(animate=False)
    app.processEvents()

check("提示：任何位置都整块在屏幕内", not bad_scr, "越界: %s" % bad_scr)
check("提示：任何位置都不被桌宠压住", not bad_pet, "被压: %s" % bad_pet)
check("提示：任何位置都不超出菜单盘", not bad_disk, "超出: %s" % bad_disk)
check("提示：文字居中在桌宠的正中轴线上（不歪）", not off_axis,
      "歪了: %s" % off_axis)
check("提示：左右贴边时改成竖向排列（横排放不进内侧窄带）",
      "吸附左" in vertical and "吸附右" in vertical, "竖排的位置: %s" % vertical)
check("提示：宽松的位置仍然用横排（不要动不动就竖排）",
      "居中" not in vertical)
pet_w.close()
app.processEvents()

# ---------- 11. 看不见的时候别烧 CPU：动图跟着可见性暂停 ----------
# 帧循环隐藏时已经降到 5fps，但 GIF/WebP 播放器有自己的定时器，隐藏期间照样
# 逐帧解码 + update() 一个看不见的窗口。只在不可见时暂停，可见时一帧不少。
src_pg = open(os.path.join(HERE, "pet_gravity.py"), encoding="utf-8").read()
check("动图：隐藏时暂停、显示时恢复（挂在 hideEvent/showEvent 上）",
      "def hideEvent" in src_pg and "_set_anim_paused(True)" in src_pg
      and "_set_anim_paused(False)" in src_pg)
check("动图：暂停用的是 set_paused，不是 stop（stop 会丢当前帧位置）",
      "def set_paused" in src_pg
      and "_webp_anim.set_paused" in src_pg)
pet_v = G.GravityPet({})
pet_v.move(-7000, -7000)
pet_v.show()
app.processEvents()
anim = pet_v._webp_anim
if anim is not None and len(getattr(anim, "_frames", [])) > 1:
    check("动图：显示时在播", anim._timer.isActive())
    pet_v.hide()
    app.processEvents()
    check("动图：隐藏时停掉了定时器（看不见就不解码）", not anim._timer.isActive())
    _idx = anim._idx
    time.sleep(0.25)
    app.processEvents()
    check("动图：暂停期间帧号不推进", anim._idx == _idx)
    pet_v.show()
    app.processEvents()
    check("动图：重新显示立刻恢复，且从当前帧接着放",
          anim._timer.isActive() and anim._idx == _idx)
else:
    # 沙箱里**明确指定了**多帧 webp，走到这里就是真出了问题（图没加载成动图），
    # 不能再像以前那样记一条"跳过"了事 —— 那正是本地 52 项、克隆里 49 项还全绿的原因
    check("动图：沙箱指定的多帧 webp 应当加载成动图（否则播放断言全被跳过）", False,
          "anim=%r" % (anim,))
pet_v.close()
app.processEvents()

shutil.rmtree(SAND, ignore_errors=True)
print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
