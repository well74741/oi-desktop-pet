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
check("打开：按网页容器的尺寸开窗（省一次回流）",
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

check("用户数据没有被测试改写", SAVED == [])
print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
