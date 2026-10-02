# -*- coding: utf-8 -*-
"""聚合AI 自动化测试：站点列表 / 摆位算法 / 宿主窗口 + 粘住的网页窗口 / 打开入口。

运行：python test_webchat.py（离屏；launcher 的存盘与 Win32 调用全部打桩，
不会碰真实的 webchat_sites.json，也不会启动浏览器）。

这里测的是"调用序列对不对"（什么时候粘、切站点是藏还是关、关窗口前有没有
先解开 owner）。真实 Win32 行为由 `_check_host.py` 拿真实 Edge 窗口验，那个要起浏览器，
不适合放进无人值守回归。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication

app = QApplication([])

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


import webchat_launcher as L
import webchat_ui
from widgets import kit

HERE_WL = os.path.dirname(os.path.abspath(__file__))

# ---------- 打桩：绝不碰用户数据、绝不开浏览器 ----------
_REAL_LOAD_SITES = L.load_sites      # 第 2 节要用真实实现测合并逻辑

SITES = [{"name": "DeepSeek", "url": "https://chat.deepseek.com"},
         {"name": "豆包", "url": "https://www.doubao.com/chat/"},
         {"name": "Kimi", "url": "https://www.kimi.com/"}]
SAVED = []
CALLS = []       # 嵌入相关的调用流水，顺序就是正确性
STATE = {"active": (None, None), "client": (960, 700), "last": None,
         "tracked": {}, "dead": set()}

L.load_sites = lambda: [dict(s) for s in SITES]
L.save_sites = lambda s: SAVED.append(list(s))
L.active = lambda: STATE["active"]
L.tracked = lambda: dict(STATE["tracked"])
L.window_alive = lambda h: h not in STATE["dead"]
L.client_size = lambda h: STATE["client"]
L.window_title = lambda h: "页面标题"
L.last_site = lambda: STATE["last"]
L.set_last_site = lambda n: STATE.update(last=str(n))
L.set_preferred_size = lambda w, h: CALLS.append(("save_size", (w, h)))
L.preferred_size = lambda: (960, 720)
L.browser_path = lambda: (r"C:\fake\msedge.exe", "Edge")
L.on_top = lambda: False
L.focus_browser = lambda h: None


def _rec(tag):
    return lambda *a, **k: (CALLS.append((tag, a[0] if a else None)), True)[1]


L.glue_browser = lambda h, owner: (CALLS.append(("glue", h)), True)[1]
L.unglue_browser = _rec("unglue")
L.owner_of = lambda h: STATE.get("owner", 0)
L.hide_browser = _rec("hide")
L.show_browser = _rec("show")
L.fit_browser = lambda h, holder, insets=None: CALLS.append(("fit", h))
L.fit_ok = lambda h, holder, tol=3: STATE.get("fit_ok", True)
L.close_all = lambda: CALLS.append(("close_all", None))


def kinds(*want):
    return [c for c in CALLS if c[0] in want]


# ---------- 1. 摆位算法（纯函数，不碰 Qt） ----------
work = (0, 0, 1920, 1040)
r = L.compute_placement((100, 400, 80, 80), work, (960, 720))
check("摆位：桌宠靠左时窗口开在右侧", r[0] > 180 and r[2:] == (960, 720))
r = L.compute_placement((1800, 400, 80, 80), work, (960, 720))
check("摆位：桌宠靠右时窗口开在左侧", 0 <= r[0] <= 1800 - 960)
r = L.compute_placement(None, work, (960, 720))
check("摆位：没有参照时居中", r[0] == (1920 - 960) // 2)
r = L.compute_placement((10, 10, 40, 40), (0, 0, 800, 600), (960, 720))
check("摆位：窗口比工作区大时夹到左上角", r[0] == 0 and r[1] == 0)

# ---------- 2. 站点列表合并（用真实实现，只读） ----------
import data_store
_real_read_list = data_store.read_list
_stub_load = L.load_sites
data_store.read_list = lambda name: [{"name": "我的站", "url": "https://a.cn"}]
L.load_sites = _REAL_LOAD_SITES
merged = L.load_sites()
check("站点：自定义排在前面", merged[0]["name"] == "我的站")
check("站点：内置项按名字补齐",
      any(s["name"] == "DeepSeek" for s in merged) and len(merged) > 1)
data_store.read_list = lambda name: [{"name": "重复", "url": "u1"},
                                     {"name": "重复", "url": "u2"},
                                     {"url": ""}]
names = [s["name"] for s in L.load_sites()]
check("站点：同名去重、空网址丢弃", names.count("重复") == 1)
data_store.read_list = _real_read_list
L.load_sites = _stub_load

# ---------- 3. 宿主窗口 + 侧边栏布局 ----------
host = webchat_ui.WebChatHost()
host.resize(900, 600)
host.show()
app.processEvents()
bar = host.bar
check("侧边栏：每个站点一个方块",
      set(bar._tiles.keys()) == {"DeepSeek", "豆包", "Kimi"})
check("侧边栏：方块显示首字、悬停提示全名",
      bar._tiles["豆包"].text() == "豆" and bar._tiles["豆包"].toolTip() == "豆包")
check("侧边栏：方块是正方形且按界面倍率放大",
      bar._tiles["Kimi"].width() == kit.ui(bar.TILE)
      == bar._tiles["Kimi"].height())
check("侧边栏：有添加与设置按钮",
      bar._add_btn.text() == "＋" and bar._set_btn.toolTip() == "设置")
check("侧边栏：不参与全局缩放（尺寸已自算）", bar.property("oi_nozoom") is True)
check("侧边栏：不再有展开/收纳（常驻）",
      not hasattr(bar, "toggle") and not hasattr(bar, "_tab"))

# 这一版的重点：侧边栏占真实布局宽度，网页容器只拿剩下的——不是盖在网页上
check("布局：侧边栏在左、宽度固定",
      bar.x() == 0 and bar.width() == kit.ui(bar.PANEL_W))
check("布局：网页容器从侧边栏右边开始（网页被挤窄，不是被盖住）",
      host.holder.x() >= bar.width())
check("布局：网页容器吃掉剩下的全部宽度",
      host.holder.x() + host.holder.width() == host.width())
check("布局：网页容器有真实 HWND（要按它的屏幕矩形摆网页窗口）",
      host.holder.testAttribute(webchat_ui.Qt.WA_NativeWindow))
check("宿主：没有跟随用的高频心跳（跟随走 moveEvent，不靠轮询）",
      host.POLL_MS >= 500)

# ---------- 4. 接管网页 ----------
CALLS.clear()
STATE["active"] = ("DeepSeek", 1001)
STATE["tracked"] = {"DeepSeek": 1001}
host.attach(1001)
check("接管：把网页窗口粘到宿主上", ("glue", 1001) in CALLS)
check("接管：摆好之后才显示出来",
      [c[0] for c in CALLS].index("fit") < [c[0] for c in CALLS].index("show"))
check("接管：记下来了", host._hwnd == 1001 and 1001 in host._pages)

# ---------- 5. 切站点：藏旧的，**绝不关** ----------
# 回归：这些网页窗口同属一个浏览器进程，关掉其中一个会把整个进程带走、剩下的
# 页面跟着全没（实测）。所以切换只能藏。
CALLS.clear()
STATE["active"] = ("Kimi", 2002)
STATE["tracked"] = {"DeepSeek": 1001, "Kimi": 2002}
host.attach(2002)
check("切站点：新页面粘上来并显示",
      ("glue", 2002) in CALLS and ("show", 2002) in CALLS)
check("切站点：旧页面只是藏起来", ("hide", 1001) in CALLS)
check("切站点：旧页面绝不关掉（关了会带走整个浏览器进程）",
      not kinds("close_all") and not kinds("unglue"))
check("切站点：两个页面都还在册", host._pages == {1001, 2002}
      and host._hwnd == 2002)
# 回归：新页面必须"先摆好再显示"，且旧页面等新的显示出来之后才藏——
# 反过来的话，要么浏览器自己那条标题栏闪一下，要么中间露出一片空白。
seq = [c[0] for c in CALLS]
check("切站点：先摆位、再显示（不会闪出浏览器标题栏）",
      seq.index("fit") < seq.index("show"))
check("切站点：新页面显示出来之后才藏旧的（中间不留空白）",
      seq.index("show") < seq.index("hide"))

# 摆位还没对齐时，绝不能把页面显示出来
CALLS.clear()
STATE["fit_ok"] = False
STATE["active"] = ("豆包", 3003)
STATE["tracked"] = {"DeepSeek": 1001, "Kimi": 2002, "豆包": 3003}
host.attach(3003)
check("切站点：没对齐就一直不显示（浏览器标题栏不会闪出来）",
      ("glue", 3003) in CALLS and not kinds("show") and kinds("fit"))
check("切站点：没对齐时旧页面还留在画面上", not kinds("hide"))
STATE["fit_ok"] = True
app.processEvents()
import time as _t
_dl = _t.monotonic() + 3
while _t.monotonic() < _dl and not [c for c in CALLS if c[0] == "show"]:
    app.processEvents()
    _t.sleep(0.02)
check("切站点：对齐之后自动显示出来", ("show", 3003) in CALLS)
check("切站点：显示之后才把旧的藏掉",
      ("hide", 2002) in CALLS
      and [c[0] for c in CALLS].index("show")
      < [c[0] for c in CALLS].index("hide"))
STATE["active"] = ("Kimi", 2002)
host.attach(2002)
app.processEvents()

# 切回去：不再重新 attach，直接显示
CALLS.clear()
STATE["active"] = ("DeepSeek", 1001)
host.attach(1001)
check("切回去：已经粘过的不再重复粘", not kinds("glue"))
check("切回去：显示旧的、藏新的",
      ("show", 1001) in CALLS and ("hide", 2002) in CALLS)

# ---------- 6. 页面自己没了 ----------
CALLS.clear()
STATE["dead"] = {1001}
host._tick()
check("页面崩了：自动切到还活着的那个",
      host._hwnd == 2002 and 1001 not in host._pages)
STATE["dead"] = {1001, 2002, 3003}
STATE["tracked"] = {}
host._tick()
app.processEvents()
check("页面全没了：宿主也收起来", not host.isVisible())

# ---------- 7. 关窗口：必须先解开 owner 再关 ----------
host2 = webchat_ui.WebChatHost()
host2.resize(900, 600)
host2.show()
app.processEvents()
STATE["dead"] = set()
STATE["active"] = ("DeepSeek", 3003)
STATE["tracked"] = {"DeepSeek": 3003}
host2.attach(3003)
STATE["active"] = ("Kimi", 4004)
STATE["tracked"] = {"DeepSeek": 3003, "Kimi": 4004}
host2.attach(4004)
CALLS.clear()
host2.close()
app.processEvents()
seq = [c[0] for c in CALLS]
check("关窗口：所有页面先解开 owner，再统一关",
      set(c[1] for c in CALLS if c[0] == "unglue") == {3003, 4004}
      and "close_all" in seq
      and seq.index("close_all") > max(i for i, k in enumerate(seq)
                                       if k == "unglue"))
# 回归：解开 owner 那一瞬间窗口会变成一个普通窗口，还显示着就会露个面
for _h in (3003, 4004):
    _hi = [i for i, c in enumerate(CALLS) if c == ("hide", _h)]
    _di = [i for i, c in enumerate(CALLS) if c == ("unglue", _h)]
    check("关窗口：页面 %d 先藏再解开（否则会露个面才消失）" % _h,
          bool(_hi) and bool(_di) and _hi[0] < _di[0])
check("关窗口：记住窗口大小", ("save_size", (900, 600)) in CALLS)

# ---------- 8. 站点增删后重建 ----------
SITES.append({"name": "通义千问", "url": "https://www.qianwen.com/"})
bar.refresh()
check("重建：新增站点出现在栏里", "通义千问" in bar._tiles)
SITES.pop()
bar.refresh()
check("重建：删掉的站点消失", "通义千问" not in bar._tiles)

# ---------- 9. 高亮 ----------
STATE["active"] = ("Kimi", 2002)
bar.refresh_active(force=True)
check("高亮：跟着活动站点走",
      "rgba(74,144,226,150)" in bar._tiles["Kimi"].styleSheet()
      and "rgba(74,144,226,150)" not in bar._tiles["DeepSeek"].styleSheet())

# ---------- 10. 「打开」入口：不弹列表，直接开上次那个 ----------
OPENED = []
webchat_ui.opener().open = (lambda site, size, finished=None:
                            (OPENED.append((site, size)), True)[1])
webchat_ui._HOST = host2      # 已经关掉的那个，open_site_ui 会照常复用

STATE["active"] = (None, None)
STATE["tracked"] = {}
STATE["last"] = "Kimi"
webchat_ui.open_webchat(None)
check("打开：没有窗口时开上次用的站点",
      OPENED and OPENED[-1][0]["name"] == "Kimi")
# 开窗尺寸按模式分：旧架构有网页容器，就按容器尺寸开（省一次回流）；
# 贴边栏模式下没有容器（浏览器窗口自己就是那个窗口），用上次记住的尺寸。
if webchat_ui.dock_mode():
    check("打开（贴边栏模式）：按上次记住的窗口尺寸开",
          OPENED[-1][1] == L.preferred_size())
else:
    check("打开（宿主模式）：按网页容器的尺寸开窗（省一次回流）",
          OPENED[-1][1] == STATE["client"])

STATE["last"] = "已经删掉的站"
webchat_ui.open_webchat(None)
check("打开：上次那个站点没了就退回列表第一个",
      OPENED[-1][0]["name"] == SITES[0]["name"])

STATE["last"] = None
STATE["active"] = ("豆包", 5005)
STATE["tracked"] = {"豆包": 5005}
webchat_ui.open_webchat(None)
check("打开：已经开着就回到那个页面（不另开一个）",
      OPENED[-1][0]["name"] == "豆包")

L.browser_path = lambda: (None, None)
_warned = []
_real_warn = kit.warn
kit.warn = lambda *a, **k: _warned.append(a)
check("打开：没装浏览器时提示而不是静默失败",
      webchat_ui.open_webchat(None) is False and len(_warned) == 1)
kit.warn = _real_warn
L.browser_path = lambda: (r"C:\fake\msedge.exe", "Edge")

try:
    host2.close()
except Exception:
    pass

# ---------- 11. 输入法：网页窗口必须保持顶层，并且跟着宿主走 ----------
# 回归：曾经把网页窗口 SetParent 成宿主的子窗口，跟随是零延迟的，但**中文输入法
# 废了**——活动窗口归桌宠线程、键盘焦点归浏览器线程，而 Windows 的输入法 UI 按
# 活动窗口所在线程走，够不到网页，于是退化成自己那套浮动候选窗，飘在屏幕角上。
# 现在改成"顶层窗口 + owner 粘住 + 每次 move 自己摆位"。
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "webchat_launcher.py"), encoding="utf-8").read()
glue = src[src.index("def glue_browser"):src.index("def owner_of")]
check("输入法：粘住网页窗口时绝不 SetParent 成子窗口（那样输入法会废）",
      "SetParent(ctypes.c_void_p(hwnd), ctypes.c_void_p(owner))" not in glue
      and "_GWL_HWNDPARENT" in glue)
check("输入法：用 owner 关系粘住（z 序跟着宿主、随宿主一起最小化）",
      "_GWL_HWNDPARENT" in glue and "SetWindowLongPtrW" in glue)
fitsrc = src[src.index("def fit_browser"):src.index("def _clip_to_content")]
check("裁剪：顶层窗口自己用窗口区域裁掉浏览器标题栏（没有父窗口帮它裁）",
      "_clip_to_content" in fitsrc and "window_rect(holder)" in fitsrc)

STATE["dead"] = set()
host3 = webchat_ui.WebChatHost()
host3.resize(900, 600)
host3.move(300, 200)
host3.show()
app.processEvents()
STATE["active"] = ("DeepSeek", 6006)
STATE["tracked"] = {"DeepSeek": 6006}
CALLS.clear()
host3.attach(6006)
app.processEvents()
CALLS.clear()
host3.move(640, 420)
app.processEvents()
check("跟随：宿主一动就立刻摆位（顶层窗口不会自己跟随，且不能防抖）",
      ("fit", 6006) in CALLS)
CALLS.clear()
host3.resize(820, 560)
app.processEvents()
check("跟随：改窗口大小也立刻重摆", ("fit", 6006) in CALLS)
ui = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "webchat_ui.py"), encoding="utf-8").read()
mv = ui[ui.index("    def moveEvent(self, ev):"):]
mv = mv[:mv.index("    # ---------- 粘住网页窗口 ----------")]
check("跟随：moveEvent 里直接 refit，没有定时器（拖动时页面不掉队）",
      "self.refit()" in mv and "Timer" not in mv)
try:
    host3.close()
except Exception:
    pass
app.processEvents()

# ---------- 内缩量绝不退回 (0,0,0,0) ----------
# 回归：fit_browser 原先写的是 `browser_insets(hwnd) or (0, 0, 0, 0)`。零内缩等于
# "这窗口没有标题栏"，于是既不往外撑也不裁——浏览器自己那条标题栏就占住内容区
# 顶部、整页往下错位，聚合AI 的侧边栏也被盖掉（用户反馈"侧边栏又没对齐了，
# 标题被分界线切断"）。缩放/切站点的一瞬间渲染子窗口查不到是常事，这时必须用
# 上一次量到的好值。
import webchat_launcher as _WL_stubbed                        # noqa: E402,F401
import importlib.util                                        # noqa: E402

# 这个套件前面把 L.fit_browser 打了桩（第 70 行左右），直接用会拿到桩。
# 单独加载一份全新的模块实例来测真实实现，两边互不干扰。
_spec = importlib.util.spec_from_file_location(
    "_wl_fresh", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "webchat_launcher.py"))
_WL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_WL)

# ---------- 不变式：当前页面活着就必须看得见（主区域不许空白） ----------
# 回归（用户报"点开聚合AI 主区域一片空白"）：页面被接管了（标题栏都换成站点名了）
# 却一直藏着。_reveal 那条链路有好几种半路作废的情形（0.8 秒内又切了站点、接管
# 被重入、显示那一步被跳过……），任何一种都会让页面永远藏着，之后再没人管它。
# 与其逐个堵竞态，不如让心跳守住这条不变式。
_vis_state = {"v": False}
_hid = []
L.window_visible = lambda h: _vis_state["v"]
L.show_browser = lambda h: (_vis_state.__setitem__("v", True),
                            CALLS.append(("show", h)), True)[2]
L.hide_browser = lambda h: (_hid.append(h), CALLS.append(("hide", h)), True)[2]
L.fit_ok = lambda h, holder, tol=3: True

_hb = webchat_ui.WebChatHost()
_hb.resize(900, 600)
_hb.show()
app.processEvents()
_hb._hwnd = 5001
_hb._pages = {5001, 5002}
CALLS.clear()
_vis_state["v"] = False          # 页面藏着 —— 就是"主区域空白"那个状态
_hb._tick()
check("主区域不空白：心跳发现当前页面藏着就把它显示出来",
      ("show", 5001) in CALLS)
check("主区域不空白：顺手把别的页面藏好（只留当前这个）",
      5002 in _hid and 5001 not in _hid)
CALLS.clear()
_hb._tick()
check("主区域不空白：已经可见时不会每拍都重复 show（不抖）",
      not [c for c in CALLS if c[0] == "show"])
_vis_state["v"] = False
_hb.hide()
app.processEvents()
CALLS.clear()
_hb._tick()
check("主区域不空白：宿主自己都没显示时不去碰页面",
      not [c for c in CALLS if c[0] == "show"])
_hb.close()
app.processEvents()

# ---------- 摆位路径绝不阻塞主线程 ----------
# 回归（用户报"打开聚合AI 除 deepseek 外整个桌宠非常卡，拖动时页面和侧边栏
# 分离"）：fit_browser 里曾经有一句 browser_insets(hwnd, timeout=0.25)，
# 而 moveEvent 每一帧都走 fit_browser —— 实测那一句在量不到时要 273ms，
# 每帧预算只有 16.7ms，超了 16 倍，于是拖动时主线程僵住、页面跟不上容器。
import time as _t_blk                                        # noqa: E402

_real_fd = L._find_descendant
L._find_descendant = lambda p, c: None       # 最坏情况：渲染子窗口永远查不到
L._INSETS_CACHE.clear() if hasattr(L, "_INSETS_CACHE") else None
try:
    _t0 = _t_blk.perf_counter()
    for _ in range(50):
        L.browser_insets(4242)               # 摆位路径用的就是默认 timeout
    _per = (_t_blk.perf_counter() - _t0) / 50.0 * 1000.0
finally:
    L._find_descendant = _real_fd
check("摆位：量内缩量默认不等待（单次 %.2f ms，一帧预算 16.7ms）" % _per,
      _per < 5.0)
_src_wl = open(os.path.join(HERE_WL, "webchat_launcher.py"),
               encoding="utf-8").read()
_fit_blk = _src_wl[_src_wl.index("def fit_browser"):
                   _src_wl.index("def _clip_to_content")]
check("摆位：fit_browser 里没有任何带 timeout 的等待（moveEvent 每帧都走它）",
      "timeout=" not in _fit_blk)
_src_ui = open(os.path.join(HERE_WL, "webchat_ui.py"), encoding="utf-8").read()
check("摆位：量不到时改为挂定时器异步重试，而不是原地等",
      "_retry_fit" in _src_ui and "FIT_RETRY_TRIES" in _src_ui)
check("摆位：显示之后仍然会继续纠正对齐（慢站点 0.8s 内起不来也能自愈）",
      "if not L.fit_ok(hwnd, self.holder_hwnd()):" in _src_ui)
check("摆位：定时心跳里有对齐兜底自愈（错位不会变成永久状态）",
      "not self._L.fit_ok(self._hwnd, self.holder_hwnd())" in _src_ui)

_WL._INSETS_CACHE.clear()
_fit_args = []
# 只桩掉最底层的两个 win32 查询，让真实的 browser_insets / fit_browser 跑起来
# （缓存是在 browser_insets 内部填的，把它整个打桩就测不到东西了）。
_HWND, _CHILD, _HOLDER = 4242, 1111, 9999
_RECTS = {_HWND: (92, 169, 616, 439),      # 窗口（含标题栏/边框）
          _CHILD: (100, 200, 600, 400),    # 渲染子窗口 = 内容区
          _HOLDER: (100, 200, 600, 400)}   # 容器
_have_child = [True]
_WL.place_child = lambda h, r: _fit_args.append(("place", r))
_WL._clip_to_content = lambda h, ins, w, hh: _fit_args.append(("clip", ins))
_WL.window_rect = lambda h: _RECTS.get(int(h))
_WL._find_descendant = lambda p, c: (_CHILD if _have_child[0] else None)
_WL.SetWindowRgn_stub = None

_ins1 = _WL.fit_browser(_HWND, _HOLDER)
check("内缩：量到了就按它摆位", _ins1 == (8, 31, 8, 8))
check("内缩：量到的值被记住了", _WL.last_insets(_HWND) == (8, 31, 8, 8))
check("内缩：窗口往左上撑出标题栏的量，内容正好压住容器",
      ("place", (92, 169, 616, 439)) in _fit_args)

# 缩放/切站点的一瞬间渲染子窗口查不到 —— 必须复用上次的好值，不能变成零
_fit_args[:] = []
_have_child[0] = False
_ins2 = _WL.fit_browser(_HWND, _HOLDER)
check("内缩：量不到时复用上次的好值，不退回 (0,0,0,0)", _ins2 == (8, 31, 8, 8))
check("内缩：复用时照样裁掉标题栏（不是整窗口都露出来）",
      ("clip", (8, 31, 8, 8)) in _fit_args)
check("内缩：复用时摆位和量到时完全一致（页面不会错位）",
      ("place", (92, 169, 616, 439)) in _fit_args)

# 从没量到过：宁可不摆，也不要用零内缩摆错
_fit_args[:] = []
check("内缩：从没量到过就不乱摆（返回 None，等下一次 refit）",
      _WL.fit_browser(7777, _HOLDER) is None and not _fit_args)

_WL.forget_insets(_HWND)
check("内缩：解绑/清理后缓存没了（HWND 复用不会继承旧内缩）",
      _WL.last_insets(_HWND) is None)

# ---------- 贴边栏模式（方案一）：开关与接线 ----------
# 真实窗口行为由 _check_dock.py 验（要起浏览器）；这里只守"接线对不对"。
import webchat_dock                                            # noqa: E402

_src_dock = open(os.path.join(HERE_WL, "webchat_dock.py"),
                 encoding="utf-8").read()
check("贴边栏：SetParent 之前先置 WS_CHILD（只调 SetParent 的话挂不上）",
      "_WS_CHILD" in _src_dock
      and _src_dock.index("SetWindowLongW") < _src_dock.index("SetParent(ctypes"))
check("贴边栏：几何用 Win32 摆，不用 Qt 的 setGeometry（会被布局改回去）",
      "SetWindowPos" in _src_dock)
check("贴边栏：有低频定时器盯父窗口尺寸（位置系统管，尺寸不管）",
      "SYNC_MS" in _src_dock and "_sync" in _src_dock)
check("贴边栏：挂接掉了会重新挂（Edge 重建窗口）",
      "_reparent()" in _src_dock and "GetParent" in _src_dock)
check("贴边栏：默认收成窄边，鼠标移上去才展开（不长期盖住页面）",
      "EDGE_W" in _src_dock and "enterEvent" in _src_dock)

_real_ls = None
try:
    import pet_gravity as _PGW
    _real_ls = _PGW.load_settings
    _PGW.load_settings = lambda: {"webchat_dock": True}
    check("贴边栏：开关为真时 dock_mode() 成立", webchat_ui.dock_mode())
    _PGW.load_settings = lambda: {"webchat_dock": False}
    check("贴边栏：开关为假时退回旧宿主架构", not webchat_ui.dock_mode())
    _PGW.load_settings = lambda: {}
    check("贴边栏：没设过这个键时默认启用", webchat_ui.dock_mode())
finally:
    if _real_ls is not None:
        _PGW.load_settings = _real_ls

check("贴边栏：launcher 提供了 restore_browser（把窗口从隐藏坐标摆回屏内）",
      callable(getattr(L, "restore_browser", None)))
_src_wl2 = open(os.path.join(HERE_WL, "webchat_launcher.py"),
                encoding="utf-8").read()
check("贴边栏：restore_browser 会处理最小化并重新摆位",
      "IsIconic" in _src_wl2 and "compute_placement(near" in _src_wl2)

# ---------- 贴边栏：切站点 / 窗口被关掉 / 收起态 ----------
# 对应用户反馈：切模型会弹出新页面、全关之后再开就没有侧边栏了、收起是硬裁一条。
# 真实窗口行为由 _check_dock_life.py 验（19 项，要起浏览器）；这里守接线。
_src_ui2 = open(os.path.join(HERE_WL, "webchat_ui.py"), encoding="utf-8").read()
_blk_at = _src_ui2[_src_ui2.index("def attach_dock("):]

check("贴边栏：切站点时把别的网页窗口藏起来（否则每切一次多一个顶层窗口）",
      callable(getattr(L, "hide_others", None))
      and "hide_others(hwnd)" in _blk_at)
check("贴边栏：切站点是就地换内容（新窗口摆到上一个窗口的矩形上）",
      callable(getattr(L, "place_browser", None))
      and "place_browser(hwnd, prev_rect)" in _blk_at)
check("贴边栏：已经在屏上的窗口不再 restore（点当前站点不该让窗口跳一下）",
      "_on_screen(hwnd)" in _blk_at)

# 父窗口销毁会连带销毁子窗口 —— 这是"再次打开没有侧边栏"的根因
check("贴边栏：能判断自己的原生窗口是不是已被系统连带销毁",
      callable(getattr(webchat_dock.DockBar, "is_dead", None)))
check("贴边栏：dock() 发现尸体会换一个新的",
      "is_dead()" in _src_ui2 and "_DOCK = None" in _src_ui2)
check("贴边栏：挂接失败有「整个重建」的兜底（不能让用户没有侧边栏）",
      callable(getattr(webchat_ui, "drop_dock", None))
      and "drop_dock()" in _blk_at)
check("贴边栏：detach 是 _reparent 的逆操作（清 WS_CHILD 补回 WS_POPUP）",
      "_WS_CHILD) | _WS_POPUP" in _src_dock)
check("贴边栏：父窗口没了就停手，不对废句柄继续操作",
      "forget_native" in _src_dock)
check("贴边栏：能报出自己挂在哪个窗口上（切站点要拿它的矩形）",
      callable(getattr(webchat_dock.DockBar, "parent_hwnd", None)))

# 收起态：不能是"把全宽的栏硬裁一条"
check("贴边栏：收起时整条栏滑出去并隐藏，不是被窗口裁掉半个按钮",
      "self.bar.setGeometry(w - self._full_w" in _src_dock
      and "setVisible(show_bar)" in _src_dock)
check("贴边栏：窄边自己画把手（收起态是常态，要看着是刻意设计的一条边）",
      "def paintEvent" in _src_dock and "drawRoundedRect" in _src_dock)
check("贴边栏：滑出/滑回是动画，缓动用桌宠统一那条曲线",
      "ease_in_out" in _src_dock and "_slide_tick" in _src_dock)
check("贴边栏：窄边宽度够点得到，也不至于挡住页面",
      10 <= webchat_dock.DockBar.EDGE_W <= 28)
# 收起态要尽量少挡页面：Edge 不给第三方留内容区，挤不窄页面，只能自己少占地方
check("贴边栏：收起态只占左缘中间一小段高度，不贴满整条边",
      30 <= webchat_dock.DockBar.HANDLE_H <= 120
      and "_handle_h" in _src_dock and "HANDLE_H" in _src_dock)
check("贴边栏：收起态半透明（用户要的 ~15% 透明）",
      200 <= webchat_dock.DockBar.COLLAPSED_ALPHA <= 230)
check("贴边栏：半透明走 Qt 的 setWindowOpacity（自己置 WS_EX_LAYERED 会被 Qt 覆盖）",
      "setWindowOpacity" in _src_dock
      and "SetLayeredWindowAttributes" not in _src_dock)
check("贴边栏：宽高和纵向位置用同一个缓动量一起插值（整体展开，不是先跳高再变宽）",
      "self._k" in _src_dock and "_slide_from + (self._slide_to" in _src_dock)

check("用户数据没有被测试改写", SAVED == [])
print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
