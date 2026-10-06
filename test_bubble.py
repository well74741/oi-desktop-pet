# -*- coding: utf-8 -*-
"""气泡模块自动化测试：折叠动画 / 消息导航 / 会话持久化 / 增量刷新 / 拆分再导出。

运行：python test_bubble.py（离屏，不触碰真实聊天记录）
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

app = QApplication([])

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# ---------- 1. 拆分再导出 ----------
import bubble_ui
import status_monitor
import bubble_layout
import module_core
import data_store
from widgets import load_module_widget
from bubble_ui import StatusBubble, ChatPanel, _chat_html

check("status_monitor 再导出 ChatPanel/StatusBubble",
      status_monitor.ChatPanel is ChatPanel and status_monitor.StatusBubble is StatusBubble)
check("bubble_layout 直接引用 bubble_ui",
      bubble_layout.ChatPanel is ChatPanel)

# ---------- 2. 聊天 HTML / 代码块 ----------
blocks = []
html = _chat_html("```python\nprint(1)\n```", blocks)
check("代码块解析与表格渲染", blocks == ["print(1)"] and "<table" in html)
check("代码块铺满宽度", "width='100%'" in html)

# ---------- 3. 折叠动画 ----------
class FakeBubble:
    _fold_locked = False
    _mv_timer = type("T", (), {"stop": lambda self: None})()

    def __init__(self):
        self.wrap_heights = []
        self.bottoms = []
        self.relayouts = 0
        self.panel = None

    def _current_side(self):
        return "above"

    def x(self):
        return 100

    def y(self):
        return 100

    def height(self):
        return self.wrap_heights[-1] if self.wrap_heights else 0

    def _fold_set_height(self, panel, wh):
        # 真实气泡每帧就是把模块摆到动画要求的高度，这里照做，
        # 才能量到输入区（对话框栏）在动画期间是不是被拉伸/压扁
        self.wrap_heights.append(int(wh))
        try:
            panel.setFixedHeight(int(wh))
            panel.layout().activate()
            self.bottoms.append(panel._bottom.height())
        except Exception:
            pass

    def _relayout(self):
        self.relayouts += 1


p = ChatPanel(None)
p.resize(194, 204)
msgs = [{"role": "user" if i % 2 else "assistant",
         "text": "第 %d 条消息" % i, "image": None, "images": []}
        for i in range(20)]
p._messages = msgs
p.rebuild()
b = FakeBubble()
p._bubble = b
p.on_resize = b._relayout
p.show()
app.processEvents()

# 起手不许跳：动画第一帧必须正好等于点击前的外框高度。收起态比展开态多一行
# 摘要标签，以前用"终局基座"算第一帧，实测收起先往上蹿 47px、展开先塌 22px，
# 然后才开始动——就是"窗口跳了一帧才开始动画"。
_h_before_fold = p.current_height()
b.wrap_heights = []
b.bottoms = []
p.toggle_collapse()
dl = time.time() + 1
while time.time() < dl and p._fold_anim is not None:
    app.processEvents()
    time.sleep(0.02)
app.processEvents()
# 容差按**总位移的比例**给，不卡死 1px：定时器第一拍总会比 t0 晚几毫秒，
# 缓动那时已经走了一两像素（机器负载高时更明显，实测打包解释器上是 2px）。
# 要守的是"没有跳"——原缺陷是首帧一下蹿 47px，和这个量级差一个数量级。
_fold_span = abs(_h_before_fold - p.current_height())
_fold_tol = max(3, int(_fold_span * 0.05))
check("收起：动画首帧 ≈ 点击前高度，不先跳一下（%d vs %d，容差 %d）"
      % (b.wrap_heights[0] if b.wrap_heights else -1, _h_before_fold, _fold_tol),
      bool(b.wrap_heights)
      and abs(b.wrap_heights[0] - _h_before_fold) <= _fold_tol)
# 输入区（对话框栏）必须全程保持终局高度。以前收起态那一行摘要是等动画结束才
# 出现的，整段动画里它的空位（约一行 + 一道间距）没人占，QVBoxLayout 就分给了
# 可伸缩的输入区 —— "消息栏在收，输入框却一直拉伸着，最后一刻才弹回去"。
_final_bottom = p._bottom.height()
check("收起：输入区全程保持终局高度，不在动画里被拉伸（最大偏离 %d px）"
      % (max(abs(x - _final_bottom) for x in b.bottoms) if b.bottoms else -1),
      bool(b.bottoms)
      and max(abs(x - _final_bottom) for x in b.bottoms) <= 3)
check("收起：末帧 = 面板自报的终局高度（不用最后再补一次）",
      bool(b.wrap_heights)
      and abs(b.wrap_heights[-1] - p.current_height()) <= 1)
check("收起：摘要行参与了动画（高度从 0 长到一行，不是突然冒出来）",
      p.collapsed_label.isVisibleTo(p)
      and p.collapsed_label.height() == p._collapsed_label_full_h())
check("收起动画完成", p._collapsed and not b._fold_locked
      and len(b.wrap_heights) > 5
      and b.wrap_heights[-1] <= b.wrap_heights[0])
check("折叠按钮朝上", not p.fold_btn._down)

_h_before_unfold = p.current_height()
b.wrap_heights = []
b.bottoms = []
p.toggle_collapse()
dl = time.time() + 1
while time.time() < dl and p._fold_anim is not None:
    app.processEvents()
    time.sleep(0.02)
app.processEvents()
_unfold_span = abs(_h_before_unfold - p.current_height())
_unfold_tol = max(3, int(_unfold_span * 0.05))
check("展开：动画首帧 ≈ 点击前高度，不先塌一下（%d vs %d，容差 %d）"
      % (b.wrap_heights[0] if b.wrap_heights else -1, _h_before_unfold,
         _unfold_tol),
      bool(b.wrap_heights)
      and abs(b.wrap_heights[0] - _h_before_unfold) <= _unfold_tol)
check("展开：全程单调变高，不来回抖",
      all(b.wrap_heights[i] <= b.wrap_heights[i + 1] + 1
          for i in range(len(b.wrap_heights) - 1)))
_final_bottom2 = p._bottom.height()
check("展开：输入区全程保持终局高度（最大偏离 %d px）"
      % (max(abs(x - _final_bottom2) for x in b.bottoms) if b.bottoms else -1),
      bool(b.bottoms)
      and max(abs(x - _final_bottom2) for x in b.bottoms) <= 3)
check("展开：末帧 = 面板自报的终局高度",
      bool(b.wrap_heights)
      and abs(b.wrap_heights[-1] - p.current_height()) <= 1)
check("展开：摘要行收到 0 之后被藏掉（不占位）",
      not p.collapsed_label.isVisibleTo(p))
check("展开动画完成", not p._collapsed and not b._fold_locked)
check("折叠按钮朝下", p.fold_btn._down)
# _base_h 必须夹上 maximumHeight：折叠动画把摘要行 setFixedHeight(0)，而它的
# sizeHint 还是一行字的高度，不夹就会多算一行（实测展开末帧多出 24px）。
p.collapsed_label.show()
p.collapsed_label.setFixedHeight(0)
_b0 = p._base_h()
p.collapsed_label.setFixedHeight(p._collapsed_label_full_h())
_b1 = p._base_h()
p.collapsed_label.hide()
p.collapsed_label.setFixedHeight(p._collapsed_label_full_h())
check("基座高度：摘要行被压到 0 时不再按 sizeHint 多算一行（%d < %d）"
      % (_b0, _b1), _b0 < _b1)

# ---------- 4. 消息导航 ----------
p._messages = msgs
p.rebuild()
sb = p.history.verticalScrollBar()
sb.setValue(sb.maximum())
app.processEvents()
top0 = p._current_top_msg_index()
p._nav_message(-1)
app.processEvents()
check("上一条消息导航", p._nav_active and p._nav_idx == top0 - 1)
p._nav_message(1)
app.processEvents()
check("下一条消息导航", p._nav_idx == top0)

# ---------- 5. 会话持久化 ----------
tmp = os.path.join(tempfile.gettempdir(), "oi_test_chat_history.json")
if os.path.exists(tmp):
    os.remove(tmp)
bubble_ui._chat_history_path = lambda: tmp


class Prov:
    def __init__(self, rid):
        self.rule = {"id": rid, "greeting": "你好"}


p1 = ChatPanel(None)
p1.set_provider(Prov("r_test"))
p1.add_message("user", "第一条")
p1._save_history()

with open(tmp, "r", encoding="utf-8") as f:
    data = json.load(f)
check("保存为多会话兼容格式", "sessions" in data["r_test"]
      and len(data["r_test"]["sessions"]) == 1)

p2 = ChatPanel(None)
p2.set_provider(Prov("r_test"))
check("读取对话记录", [m["text"] for m in p2._messages][-1] == "第一条")

with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"r_old": [{"role": "user", "text": "旧消息"}]}, f)
p3 = ChatPanel(None)
p3.set_provider(Prov("r_old"))
check("旧格式兼容", [m["text"] for m in p3._messages] == ["旧消息"])
os.remove(tmp)

# ---------- 6. 增量刷新（行内容未变化不重渲染） ----------
from bubble_layout import StatusBubbleLayout, _LBtnRow, _LTextRow


class FakePet2(QWidget):
    settings = {}
    is_dragging = False
    _is_pressed = False

    def __init__(self):
        super().__init__()
        self.resize(75, 75)
        self.radial_menu = type("M", (), {
            "is_visible_state": False, "_sector_outer": lambda self: 0})()
        self.move(500, 300)

    def mapToGlobal(self, p):
        return self.pos() + p

    def _cursor_over_pet(self):
        return False


bub = StatusBubbleLayout(FakePet2())
bub._disp_rows = [("CPU", "50%", None)]
bub._relayout()
row = bub._row_widgets[0]
calls = []
orig = row.set_content


def counted(title, value, avail_w, pending=False, link_href="", state=None):
    calls.append((str(title), str(value)))
    return orig(title, value, avail_w, pending=pending, link_href=link_href,
                state=state)


row.set_content = counted
bub._update_rows_inplace([("t", "CPU", "CPU", "50%", None)], 100)
bub._update_rows_inplace([("t", "CPU", "CPU", "50%", None)], 100)
bub._update_rows_inplace([("t", "CPU", "CPU", "60%", None)], 100)
check("增量刷新跳过未变化行", len(calls) == 1 and calls[-1] == ("CPU", "60%"))
check("内容签名已记录",
      row._last_content == ("CPU", "60%", False, "", "ok", ""))

# ---------- 7. 模块健康状态与设计令牌 ----------
rules = module_core.normalize_rules([
    {"name": "天气", "source": "bad-old-config"},
    {"name": "聚合AI", "source": {"action": "webchat"}},
])
check("模块配置归一化",
      isinstance(rules[0]["source"], dict)
      and rules[0]["id"] and rules[1]["id"]
      and len({r["id"] for r in rules}) == 2)
check("旧 webchat 兼容为动作模块",
      module_core.module_spec(rules[1]).is_action)

scheduler = module_core.ModuleScheduler()
sprov = status_monitor.RuleProvider(rules[0])
sname = sprov.name
check("调度器首次加载", scheduler.should_run(sprov, None, 1000.0))
scheduler.started(sname)
check("调度器并发去重",
      not scheduler.should_run(sprov, None, 1000.5))
scheduler.finish(sname, True, 1000.0)
check("失败退避与计数",
      scheduler.failure_count(sname) == 1
      and scheduler.retry_in(sname, 1001.0) == 4
      and not scheduler.should_run(sprov, None, 1001.0))
check("退避结束后可重试",
      scheduler.should_run(sprov, None, 1005.0))
scheduler.finish(sname, False, 1005.0)
check("成功后退避重置",
      scheduler.failure_count(sname) == 0
      and scheduler.retry_in(sname, 1005.0) == 0)

provider = status_monitor.RuleProvider(
    {"id": "r_health", "name": "测试模块", "interval": 60})
provider._last_error = "HTTP Error 503"
state = bub._provider_state(provider, (time.monotonic() - 10, "失败"))
check("模块错误状态", state["kind"] == "error" and state["retry_in"] <= 60)

provider._hung = True
check("脚本卡死暂停状态",
      bub._provider_state(provider, None, False)["kind"] == "paused")

provider._hung = False
provider._last_error = ""
check("首次加载状态",
      bub._provider_state(provider, None, True, time.monotonic())["kind"]
      == "loading")

error_provider = status_monitor.RuleProvider(
    {"id": "r_recover", "name": "恢复模块",
     "source": {"type": "static", "text": "OK"}})
error_provider._last_error = "上一次网络错误"
check("成功刷新清除旧错误",
      error_provider.collect() == "OK"
      and error_provider._last_error == "")

health_row = _LTextRow()
health_row.set_content("天气", "获取失败", 100,
                       state={"kind": "error", "error": "HTTP Error 503",
                              "retry_in": 8})
check("健康行状态标记", health_row._state_kind == "error"
      and "●" in health_row.title.text()
      and "HTTP Error 503" in health_row.title.toolTip())

# 启动期回归：普通规则没有交互组件，也必须有稳定标题行。
plain_provider = status_monitor.RuleProvider(
    {"id": "r_plain", "name": "普通模块", "interval": 3600,
     "source": {"type": "static", "value": "OK"}})
bub._rules = [plain_provider]
bub._inter_widgets = []
bub._pending_rules = set()
bub._chat_pending = set()
bub._last = {plain_provider.name: (time.monotonic(), "OK")}
bub._refresh_impl()
plain_row = next(row for row in bub._raw_rows
                 if row[0] == "普通模块")
health_row.set_content("正常", "OK", 100,
                       state={"kind": "ok"})
check("普通模块启动刷新", plain_row[1] == "OK" and plain_row[2] is None
      and "●" not in health_row.title.text())

# 聚合AI是动作行，必须保留“打开”按钮。
action_provider = status_monitor.RuleProvider(
    {"id": "r_action", "name": "普通模块",
     "source": {"type": "static", "text": "OK", "ui": "webchat"}})
bub._rules = [action_provider]
bub._last = {action_provider.name: (time.monotonic(), "OK")}
bub._refresh_impl()
btn_mark = next(row for row in bub._raw_rows
                if row[0] == "普通模块")[2]
check("聚合AI按钮行", btn_mark == "__btn__")
bub._disp_rows = bub._raw_rows
bub._last_keys = []
bub._row_widgets = []
bub._wrap_cache = {}
bub._btn_text = lambda: "打开"
bub._relayout()
btn_rows = [row for row in bub._row_widgets if isinstance(row, _LBtnRow)]
check("聚合AI打开按钮渲染",
      len(btn_rows) == 1 and btn_rows[0].title.text() == "普通模块"
      and btn_rows[0].btn.text() == "打开")

# 回归：LLM 聊天、内嵌体验组件和聚合 AI 必须能共存；组件分支此前因未定义
# ui 变量中断，导致后续模块（包括打开按钮）整批消失。
chat_provider = status_monitor.RuleProvider(
    {"id": "r_chat_ui", "name": "AI助手", "chat": True,
     "source": {"type": "llm", "model": "test-model"}})
counter_provider = status_monitor.RuleProvider(
    {"id": "r_counter_ui", "name": "画布",
     "source": {"type": "static", "text": "0", "ui": "canvas"}})
bub._rules = [chat_provider, counter_provider]
bub._chat_pending = {}
bub._last = {}
bub._rebuild_interactive()
chat_widget = bub._widget_for_rule(chat_provider.name)
counter_widget = bub._widget_for_rule(counter_provider.name)
check("聊天与内嵌组件共存",
      isinstance(chat_widget, ChatPanel) and counter_widget is not None)
check("未发送时聊天不是加载态",
      bub._provider_state(chat_provider, None, False,
                          time.monotonic())["kind"] == "idle")
check("内嵌组件展开入口",
      hasattr(counter_widget, "_fold_btn"))

# ---------- 缩放矩阵：FIX_H/行宽/行高必须由框架统一换算 ----------
from widgets import kit
from bubble_ui import _widget_height


class FixedModule(QWidget):
    # 237 = 旧版写的 158 × 1.5。v0.9.42 把那一次 1.5 烘进了源码，组件的 FIX_H
    # 存的就是最终像素，这里跟着改，否则 kit.bs(FIX_H) 会少放大一轮。
    FIX_H = 237


kit.set_bubble_scale(2.0)
fixed = FixedModule()
# 期望值 = 标准档像素 × 1.5 × 档位。
# 这里的 1.5 是**历史基准倍率**：v0.9.42 之前它是运行时的 kit.UI_BASE，现在已经
# 烘进源码里的每个基准值（气泡宽度 210→315 等）。下面各处仍按"标准档像素"写，
# 所以要在这里补上这一次，才能和 kit.bs() 的结果对齐。
def _x(v, lvl):
    return max(1, int(round(v * 1.5 * lvl)))


check("FIX_H 框架统一放大", _widget_height(fixed, _x(210, 2.0)) == _x(158, 2.0))

kit.set_bubble_scale(1.0)
bub1 = StatusBubbleLayout(FakePet2())
bub1._disp_rows = [("CPU", "50%", None)]
bub1._relayout()
check("标准气泡宽度", bub1.width() == _x(210, 1.0))
check("标准行高不裁切",
      bub1._row_widgets[0].minimumHeight() >= _x(15, 1.0))

kit.set_bubble_scale(2.0)
bub2 = StatusBubbleLayout(FakePet2())
bub2._disp_rows = [("CPU", "50%", None)]
bub2._relayout()
check("大气泡宽度", bub2.width() == _x(210, 2.0))
check("大气泡行高等比下限",
      bub2._row_widgets[0].minimumHeight() >= _x(15, 2.0))
check("气泡设计令牌统一缩放",
      kit.bubble_token("width") == _x(210, 2.0)
      and kit.bubble_token("title_width") == _x(44, 2.0)
      and kit.bubble_token("action_height") == _x(13, 2.0))
kit.set_bubble_scale(1.0)

# ---------- AI 对话面板：收起态不能裁切、右侧按钮要对齐 ----------
# 回归：current_height() 曾是写死的 _BASE_H=54，加了标题栏或输入框打到第二行
# 就不够高，底下那排折叠/清屏按钮被裁在面板外。
from bubble_ui import ChatPanel

for _sc in (1.0, 2.0):
    kit.set_bubble_scale(_sc)
    _host = QWidget()
    _hl = QVBoxLayout(_host)
    _hl.setContentsMargins(0, 0, 0, 0)
    _cp = ChatPanel(_host)
    _hl.addWidget(_cp)
    _cp.show()
    _host.show()
    _cp._messages = [{"role": "assistant", "text": "你好，我是 AI 助手，" * 6}]
    _cp.rebuild()
    _cp.set_header("AI助手", right_text="deepseek-chat")
    _cp._collapsed = True
    _cp.fold_btn.set_down(False)
    _cp._finish_fold()
    app.processEvents()

    def _fit(panel, tag):
        h = panel.current_height()
        panel.resize(kit.bs(315), h)
        _host.resize(kit.bs(315), h)
        app.processEvents()
        worst = 0
        for w in (panel.collapsed_label, panel.input, panel.up_btn,
                  panel.down_btn, panel.fold_btn, panel.trash_btn,
                  panel._header_widget):
            if w is None or not w.isVisibleTo(panel):
                continue
            bottom = w.mapTo(panel, w.rect().bottomLeft()).y()
            worst = max(worst, bottom - (h - 1))
        check("收起态%s(x%s)：内容不超出 current_height" % (tag, _sc), worst <= 0)
        ib = panel.input.mapTo(panel, panel.input.rect().bottomLeft()).y()
        gb = max(panel.trash_btn.mapTo(panel,
                                       panel.trash_btn.rect().bottomLeft()).y(),
                 panel.down_btn.mapTo(panel,
                                      panel.down_btn.rect().bottomLeft()).y())
        check("收起态%s(x%s)：按钮列贴输入框底边" % (tag, _sc), abs(gb - ib) <= 2)

    _fit(_cp, "带标题栏")
    _cp.input.setPlainText("一\n二\n三")     # 输入框长高也不能把按钮挤出去
    app.processEvents()
    _fit(_cp, "输入三行")
    check("收起态(x%s)：摘要行标出是谁说的" % _sc,
          _cp.collapsed_label.text().startswith("AI："))
    _cp.input.setPlainText("")
    _host.close()

kit.set_bubble_scale(1.0)

# ---------- 模块行的文字必须正好落在卡片里 ----------
# 回归：`_font_h()` 原来是"字体行高 + bs(4)"，当作行内文本控件的**最小高度**用；
# 全局字体换成微软雅黑后同字号行高 15→20px，最小值 26 超过了行高 23，布局满足
# 不了最小值就向下溢出——文字被挤到卡片下半部、底边还被裁掉。
# 这里量的是"最小高度 + 卡片上下内边距 <= 行高"这个不变式（与字体无关，离屏也
# 能守住），以及真实行几何：值区不超出卡片、标题与值同一条中线。
for _sc in (1.0, 1.5, 2.0):
    kit.set_bubble_scale(_sc)
    need = bubble_layout._font_h() + 2 * kit.bs(1.5)
    check("行高(x%s)：容得下一行字加卡片内边距（%d <= %d）"
          % (_sc, need, kit.row_height()), need <= kit.row_height())
    _h2 = QWidget()
    _l2 = QVBoxLayout(_h2)
    _l2.setContentsMargins(0, 0, 0, 0)
    _rows = []
    for _t, _v in (("CPU", "..."), ("内存", "48% (30.9/63.7 GB)")):
        _r = bubble_layout._LTextRow(_h2)
        _l2.addWidget(_r)
        _rows.append((_r, _t, _v))
    _h2.resize(int(280 * _sc), int(120 * _sc))
    _h2.show()
    app.processEvents()
    for _r, _t, _v in _rows:
        _r.set_content(_t, _v, int(150 * _sc))
    _h2.layout().activate()
    app.processEvents()
    _over, _off = 0, 0.0
    for _r, _t, _v in _rows:
        _cr, _tl, _vl = _r._card.geometry(), _r.title.geometry(), _r.value.geometry()
        _over = max(_over, (_vl.y() + _vl.height()) - (_cr.y() + _cr.height()))
        _off = max(_off, abs((_tl.y() + _tl.height() / 2.0)
                             - (_vl.y() + _vl.height() / 2.0)))
    check("模块行(x%s)：值区不超出卡片（文字不会出框）" % _sc, _over <= 0)
    check("模块行(x%s)：标题与值同一条中线" % _sc, _off <= 1.0)
    _h2.close()

kit.set_bubble_scale(1.0)

# ---------- 回车发送 ----------
# 回归：输入框原来用 `parentWidget()._send()` 发消息。0.9.07 把输入行包进了一层
# QWidget（_bottom），加进那层布局时 Qt 会把父控件改成那层 QWidget，于是
# `parentWidget()` 上没有 _send —— 打完字回车发不出去。现在走 submitted 信号。
from PyQt5.QtCore import QEvent as _QEvt, Qt as _Qt
from PyQt5.QtGui import QKeyEvent as _QKey

QtKey_Return = _Qt.Key_Return
QtShift = _Qt.ShiftModifier
QtNoMod = _Qt.NoModifier

_ep = ChatPanel()
_ep.resize(260, 120)
_ep.show()
app.processEvents()
_sent = []
_ep.set_provider(Prov("r_enter"))
_ep.on_send = lambda text, images=None: _sent.append((text, images))
_ep.input.setPlainText("你好")
app.processEvents()
app.sendEvent(_ep.input, _QKey(_QEvt.KeyPress, QtKey_Return, QtNoMod))
app.processEvents()
check("回车发送：Enter 真的把消息发出去了（不再依赖 parentWidget）",
      _sent == [("你好", [])])
check("回车发送：消息进了对话、输入框清空",
      _ep._messages and _ep._messages[-1]["text"] == "你好"
      and _ep.input.toPlainText() == "")
_before = len(_sent)
_ep.input.setPlainText("一")
app.processEvents()
app.sendEvent(_ep.input, _QKey(_QEvt.KeyPress, QtKey_Return, QtShift))
check("回车发送：Shift+Enter 只换行不发送",
      len(_sent) == _before and _ep.input.toPlainText() != "")
check("回车发送：贴图信号也接上了面板",
      _ep.input.receivers(_ep.input.image_pasted) > 0)
# 软换行 / 自动折行也要把输入框撑高，否则第二行被裁在框外
_ep.input.setPlainText("一\n二")
app.processEvents()
_doc_h = _ep.input.document().documentLayout().documentSize().height()
check("输入框：两行时装得下（按文档排版高度长高，不是只数段落）",
      _ep.input.viewport().height() >= _doc_h - 1)
_ep.input.setPlainText("")
app.processEvents()
_one = _ep.input.height()
_ep.input.setPlainText("一\n二")
app.processEvents()
check("输入框：一行时不会白占两行的高度", _ep.input.height() > _one)
_ep.close()

# ---------- 气泡分列：一列装不下就往旁边开一列 ----------
# 用户的 15 个模块全收起就接近 1080p 的可用高度，再展开两三个交互组件就会长到
# 屏幕外面去——长出去的部分既看不见也点不到。现在超高就新开一列，气泡整体以
# 桌宠为中心摆放，多出来的列自然向两边长。
from PyQt5.QtCore import QPoint as _QPt                           # noqa: E402

_cb = StatusBubbleLayout(FakePet2())
_cb._max_col_h = lambda: 260          # 真实值是屏幕可用高度，这里压小逼出分列
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(14)]
_cb._relayout()
app.processEvents()
_r = [_cb._row_rect(w) for w in _cb._row_widgets]
check("分列：装不下时开了第二列", _cb._ncols == 2)
check("分列：高度压在上限内（不会长到屏幕外）", _cb._full_h <= 260)
check("分列：宽度 = 列数 × 单列宽", _cb.width() == _cb._FIX_W * _cb._ncols)
check("分列：每一行都在气泡框内",
      all(0 <= r.left() and r.right() <= _cb.width()
          and 0 <= r.top() and r.bottom() <= _cb._full_h for r in _r))
check("分列：行互不重叠", len({(r.left(), r.top()) for r in _r}) == len(_r))
_c1 = [r for r in _r if r.left() < _cb._FIX_W]
_c2 = [r for r in _r if r.left() >= _cb._FIX_W]
check("分列：两列都有行，且第二列在右边", len(_c1) > 0 and len(_c2) > 0)
check("分列：两列顶端对齐、行距一致（短的那列把余量丢到底部）",
      _c1[0].top() == _c2[0].top()
      and (_c1[1].top() - _c1[0].top()) == (_c2[1].top() - _c2[0].top()))
check("分列：模块顺序不变（分列只换行不换序）",
      [w._ridx for w in _cb._row_widgets] == list(range(len(_cb._row_widgets))))
# 拖拽命中：第二列的行也要点得到（老代码只看 y，会点中第一列同高度那行）
check("分列：每一行的手柄都能精确命中（跨列也对）",
      all(_cb._handle_hit(_QPt(r.left() + 2, r.center().y())) is w
          for w, r in zip(_cb._row_widgets, _r)))
check("分列：点在卡片中部不会误触发拖拽",
      all(_cb._handle_hit(_QPt(r.center().x(), r.center().y())) is None for r in _r))
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(22)]
_cb._relayout()
app.processEvents()
check("分列：更多行就开第三列", _cb._ncols == 3 and _cb._full_h <= 260)
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(4)]
_cb._relayout()
app.processEvents()
check("分列：行变少会收回单列", _cb._ncols == 1 and _cb.width() == _cb._FIX_W)

# 列高要**均摊**：只按"撞到屏幕高度才换列"装的话，第一列顶到屏幕底、第二列只剩
# 零星几个，短列下面拖着一大片空白（用户反馈"这么大空间很浪费"）。
for _n in (22, 30, 45):
    _cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(_n)]
    _cb._relayout()
    app.processEvents()
    _hs = [p[3] for p in _cb._col_panels]
    check("分列均摊(%d 个模块)：各列高度接近（%s，差 %d px）"
          % (_n, _hs, max(_hs) - min(_hs)),
          len(_hs) >= 2 and (max(_hs) - min(_hs)) <= max(_hs) * 0.25)
    check("分列均摊(%d 个模块)：没有因为均摊而多开一列（%d 列）"
          % (_n, _cb._ncols),
          _cb._ncols == len(_cb._split_columns(
              [w for w in _cb._row_widgets if bubble_layout._w_is_alive(w)])))
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(4)]
_cb._relayout()
app.processEvents()

# 多列时每列是**独立一块背景**、各自按内容长短：一整块大背景会让只放一个模块
# 的第二列下面拖着一大片空白
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(10)]
_cb._relayout()
_cb.show()
app.processEvents()
_p = _cb._col_panels
check("分列背景：一列一块，不是一整块", len(_p) == _cb._ncols == 2)
check("分列背景：两块紧贴（第二块起点 = 第一块右缘）", _p[1][0] == _p[0][2])
# 每块面板的高度必须正好包住**自己那一列**的行，而不是统一取最高列的高度
# （原先这里断言"短的那列明显更短"，那是在断言分列不均摊的旧行为；列高均摊之后
# 两列本来就一样高，真正该守的不变式是"面板贴合自己列的内容"）。
_col_bottoms = []
for _pi, (_px, _py, _pw, _ph) in enumerate(_p):
    _rows_in = [_cb._row_rect(w) for w in _cb._row_widgets
                if _px <= _cb._row_rect(w).left() < _px + _pw]
    _col_bottoms.append((_ph, max((r.bottom() for r in _rows_in), default=0)))
check("分列背景：每块面板正好包住自己那列的内容（%s）" % (_col_bottoms,),
      all(0 <= ph - bot <= 12 for ph, bot in _col_bottoms))
check("分列背景：窗口高度 = 最高那列", _cb._full_h == max(x[3] for x in _p))
check("分列背景：每行都落在自己那块板子里",
      all(any(px <= r.left() and r.right() <= px + pw and r.bottom() <= py + ph
              for px, py, pw, ph in _p)
          for r in [_cb._row_rect(w) for w in _cb._row_widgets]))
_m = _cb.mask()
check("分列背景：短列下方的空白被遮罩挖掉（不挡鼠标）",
      not _m.isEmpty()
      and not _m.contains(_QPt(_p[1][0] + _p[1][2] // 2, _p[1][3] + 20))
      and _m.contains(_QPt(_p[1][0] + _p[1][2] // 2, _p[1][3] // 2)))
_cb._disp_rows = [("模块%d" % i, "值%d" % i, None) for i in range(4)]
_cb._relayout()
app.processEvents()
check("分列背景：单列时不设遮罩（和以前行为一致）", _cb.mask().isEmpty())
_cb.close()

# ---------- 组件行的展开 / 收起按钮 ----------
# 便签、待办这类长条组件占掉半个气泡，得能一键收成一条标题栏。三件事要守住：
# 状态落盘（重启还在）、动画平滑单调（不许跳、不许回弹）、收起态扛得住定时刷新
# （刷新会走 _relayout 重建所有行，实测过正在动的那一行被连根删掉）。
_fold_sand = tempfile.mkdtemp(prefix="oi_test_rowfold_")
_ds_dir_bak = data_store.DATA_DIR
data_store.DATA_DIR = _fold_sand
import pet_gravity as _PG                                    # noqa: E402
if os.path.abspath(_fold_sand) not in os.path.abspath(_PG.get_config_path()):
    print("!! 配置路径没被隔离，拒绝继续:", _PG.get_config_path())
    sys.exit(2)

_notes, _nerr = load_module_widget("notes")
check("组件行收起：便签组件能加载（%s）" % (_nerr or "ok"), _notes is not None)
_fb = StatusBubbleLayout(FakePet2())
_fb.pet.settings = {}
_fb._refresh = lambda *a, **k: None      # 测试没有真规则表，别让后台刷新清掉假数据行
for _t in ("_timer", "_mv_timer"):
    _tm = getattr(_fb, _t, None)
    if _tm is not None:
        _tm.stop()
_fb._disp_rows = [("CPU", "50%", None), ("便签", "", _notes)]
_fb._relayout()
_fb.show()
app.processEvents()
_wrap = [w for w in _fb._row_widgets
         if isinstance(w, bubble_layout._LWidgetRow)][0]
_h_open, _bub_open = _wrap.height(), _fb.height()
check("组件行收起：长条组件带上了收起按钮", hasattr(_wrap, "fold_btn")
      and _wrap.fold_btn.isVisibleTo(_wrap) and not _wrap.is_collapsed()
      and _wrap.fold_btn.text() == "收起")

_seq = []
_bubseq = []
_wrap.fold_btn.click()
_dl = time.time() + 2
while time.time() < _dl and getattr(_fb, "_rowfold", None) is not None:
    app.processEvents()
    time.sleep(0.004)
    if not _seq or _seq[-1] != _wrap.height():
        _seq.append(_wrap.height())
        _bubseq.append(_fb.height())
app.processEvents()
_steps = [_seq[i] - _seq[i + 1] for i in range(len(_seq) - 1)]
check("组件行收起：收起后只剩一条标题栏，组件被藏起来",
      _wrap.is_collapsed() and _wrap.height() == _wrap.collapsed_height()
      and not _notes.isVisible())
check("组件行收起：气泡跟着矮下去（省了 %d px）" % (_bub_open - _fb.height()),
      _fb.height() < _bub_open - 40)
# 整框必须**每一帧**跟着行走。之前整框尺寸交给 _start_settle 再跑一段 0.16s
# 补位动画：行在动、框也在动，两段各算各的时间，框慢半拍、末尾补最后几像素，
# 就是用户说的"收完还弹一下"。
_lag = [abs((_bubseq[i] - _bubseq[-1]) - (_seq[i] - _seq[-1]))
        for i in range(len(_seq))]
check("组件行收起：整框每帧都跟着行走，不慢半拍（最大偏差 %d px）"
      % (max(_lag) if _lag else 0),
      bool(_lag) and max(_lag) <= 2)
check("组件行收起：整框也是单调收窄，没有二段补位动画",
      all(_bubseq[i] >= _bubseq[i + 1] for i in range(len(_bubseq) - 1)))
check("组件行收起：动画一停，框就已经在最终尺寸上（不再补一段）",
      _fb.height() == _fb._full_h and not _fb._settle_active)
check("组件行收起：动画是一段一段走的，不是一帧切（%d 帧）" % len(_seq),
      len(_seq) >= 8)
check("组件行收起：全程单调递减，中途不回弹（最大回弹 %d px）"
      % (-min(_steps) if _steps and min(_steps) < 0 else 0),
      all(s >= 0 for s in _steps))
check("组件行收起：单帧跨度不超过总高差的三成（最大 %d / 总 %d）"
      % (max(_steps) if _steps else 0, _h_open - _wrap.height()),
      bool(_steps) and max(_steps) <= max(12, (_h_open - _wrap.height()) * 0.3))
check("组件行收起：末尾停在目标高度上，不用再补一次动画",
      _seq[-1] == _wrap.collapsed_height() and not _fb._fold_locked)
check("组件行收起：状态入库（重启后还是收起的）",
      _fb.pet.settings.get("collapsed_widgets") == ["w:1"])
_saved = json.load(open(_PG.get_config_path(), encoding="utf-8"))
check("组件行收起：状态写进了配置文件（沙箱内）",
      _saved.get("collapsed_widgets") == ["w:1"])

# 定时刷新会重建所有行——收起态必须从 settings 里重新读出来，不能被刷掉
_fb._relayout()
app.processEvents()
_wrap2 = [w for w in _fb._row_widgets
          if isinstance(w, bubble_layout._LWidgetRow)][0]
check("组件行收起：刷新重建行之后，收起态还在",
      _wrap2.is_collapsed()
      and _wrap2.height() == _wrap2.collapsed_height())
check("组件行收起：重建后按钮状态也对得上（收起态写着「展开」）",
      _wrap2.fold_btn.text() == "展开")

_wrap2.fold_btn.click()
_dl = time.time() + 2
while time.time() < _dl and getattr(_fb, "_rowfold", None) is not None:
    app.processEvents()
    time.sleep(0.004)
app.processEvents()
check("组件行收起：再点一次完整还原（行高 %d → %d）"
      % (_wrap2.collapsed_height(), _wrap2.height()),
      not _wrap2.is_collapsed() and _wrap2.height() == _h_open
      and _notes.isVisible())
check("组件行收起：展开后状态从库里清掉",
      _fb.pet.settings.get("collapsed_widgets") == [])
_fb.close()

# ---------- 组件自带「展开」按钮：两个方向都要有动画 ----------
# 以前 widget.on_resize = self._relayout（瞬间重建）：展开时后面还跟着一次整框
# 补位动画，看着像"有动画"；收起时补位动画方向相反、又被新尺寸立刻盖掉，就成了
# "一帧跳回去"。现在和行上的收起按钮共用同一条路径，两个方向必须对称。
for _wname in ("tokenmeter", "stats"):
    _w2, _werr = load_module_widget(_wname)
    if _w2 is None:
        check("组件自折叠(%s)：组件能加载" % _wname, False)
        continue
    _sb = StatusBubbleLayout(FakePet2())
    _sb.pet.settings = {}
    _sb._refresh = lambda *a, **k: None
    for _t in ("_timer", "_mv_timer"):
        _tm = getattr(_sb, _t, None)
        if _tm is not None:
            _tm.stop()
    _sb._disp_rows = [("CPU", "50%", None), (_wname, "", _w2)]
    _sb._relayout()
    _sb.show()
    app.processEvents()
    _w2.on_resize = lambda _x=_w2, _b=_sb: _b._on_widget_resize(_x)
    _btn2 = getattr(_w2, "_fold_btn", None)
    check("组件自折叠(%s)：带底色的「展开」按钮在（文案统一，不叫「详情」）" % _wname,
          _btn2 is not None and _btn2.text() == "展开")
    _dirs = {}
    for _label in ("展开", "收起"):
        _h_b = _sb._wrap_of(_w2).height()
        _s2, _b2 = [], []
        _btn2.click()
        _dl = time.time() + 2
        while time.time() < _dl and getattr(_sb, "_rowfold", None) is not None:
            app.processEvents()
            time.sleep(0.004)
            _wp = _sb._wrap_of(_w2)
            if _wp is not None and (not _s2 or _s2[-1] != _wp.height()):
                _s2.append(_wp.height())
                _b2.append(_sb.height())
        app.processEvents()
        _dirs[_label] = (_s2, _b2, _h_b)
    for _label, (_s2, _b2, _h_b) in _dirs.items():
        _st2 = [_s2[i + 1] - _s2[i] for i in range(len(_s2) - 1)]
        _lag2 = [abs((_b2[i] - _b2[-1]) - (_s2[i] - _s2[-1]))
                 for i in range(len(_s2))]
        check("组件自折叠(%s·%s)：是逐帧动画，不是一帧跳（%d 帧）"
              % (_wname, _label, len(_s2)), len(_s2) >= 8)
        check("组件自折叠(%s·%s)：全程单调，中途不回头" % (_wname, _label),
              all(s >= 0 for s in _st2) or all(s <= 0 for s in _st2))
        check("组件自折叠(%s·%s)：整框每帧跟着行走（最大偏差 %d px）"
              % (_wname, _label, max(_lag2) if _lag2 else -1),
              bool(_lag2) and max(_lag2) <= 2)
    # 按**比例**比，不按差值：动画按时间走（固定时长、16ms 一帧），机器忙时计时器
    # 会掉帧，两个方向的帧数会一起往下掉。原来写的是"差值 ≤ 3"，打包环境负载高的
    # 一次跑出过 展开 10 / 收起 14 → 误报（连跑 12 次都是 11~14、差值 ≤ 1）。
    # 这条要防的是"收起时一帧跳回去"（1 帧 vs 14 帧），比例 0.6 照样抓得住。
    _nf = (len(_dirs["展开"][0]), len(_dirs["收起"][0]))
    check("组件自折叠(%s)：两个方向帧数相当（展开 %d / 收起 %d）"
          % ((_wname,) + _nf),
          min(_nf) >= 5 and min(_nf) >= 0.6 * max(_nf))
    _sb.close()

# ---------- 拖气泡顶栏移动桌宠：不许拖出屏幕，松手要吸附 ----------
# 回归：这条路以前是裸 pet.move()，没有边界钳制也没有吸附收尾，一路拖能把桌宠
# 推到屏幕外面再也找不着（用户反馈"会被一直拖到屏幕之外去"）。直接拖桌宠本体
# 走的是 _clamp_to_desktop + _check_edge_snap，两条路必须一致。
from PyQt5.QtCore import QEvent as _QEv2, Qt as _Qt2          # noqa: E402
from PyQt5.QtGui import QMouseEvent as _QME                    # noqa: E402

_st = _PG.load_settings()
_st["status_rules"] = [{"name": "CPU",
                        "source": {"type": "static", "text": "50%"}}]
_PG.save_settings(_st)
_pet = _PG.GravityPet(_PG.load_settings())
_pet.show()
app.processEvents()
_bub = _pet.status_bubble
_bub.show()
app.processEvents()
_scr = QApplication.primaryScreen().availableGeometry()


def _drag_pet_via_bubble(gx, gy):
    _start = QPoint(_bub.x() + _bub.width() // 2, _bub.y() + 3)
    _bub.mousePressEvent(_QME(_QEv2.MouseButtonPress,
                              QPoint(_bub.width() // 2, 3), _start,
                              _Qt2.LeftButton, _Qt2.LeftButton, _Qt2.NoModifier))
    _bub.mouseMoveEvent(_QME(_QEv2.MouseMove, QPoint(0, 0), QPoint(gx, gy),
                             _Qt2.NoButton, _Qt2.LeftButton, _Qt2.NoModifier))
    app.processEvents()
    _bub.mouseReleaseEvent(_QME(_QEv2.MouseButtonRelease, QPoint(0, 0),
                                QPoint(gx, gy), _Qt2.LeftButton, _Qt2.NoButton,
                                _Qt2.NoModifier))
    app.processEvents()


def _visible_px():
    _g = _PG._virtual_geo()
    _r = _pet.frameGeometry()
    return (max(0, min(_r.right() + 1, _g.right() + 1) - max(_r.left(), _g.left())),
            max(0, min(_r.bottom() + 1, _g.bottom() + 1) - max(_r.top(), _g.top())))


_drag_cases = []
for _nm, _gx, _gy in (("左", -4000, 400), ("右", _scr.width() + 4000, 400),
                      ("上", 600, -4000), ("下", 600, _scr.height() + 4000)):
    _drag_pet_via_bubble(_gx, _gy)
    _vw, _vh = _visible_px()
    _drag_cases.append((_nm, _vw, _vh, _pet.snapped_edge))
    _pet.recenter()
    app.processEvents()

check("拖气泡移桌宠：往四个方向都拖不出屏幕（最少露出 %s px）"
      % ([min(c[1], c[2]) for c in _drag_cases],),
      all(c[1] > 0 and c[2] > 0 for c in _drag_cases))
check("拖气泡移桌宠：拖到边缘松手会变成吸附态（%s）"
      % ([c[3] for c in _drag_cases],),
      all(c[3] for c in _drag_cases))
check("拖气泡移桌宠：和直接拖桌宠用的是同一套钳制/吸附（不是各写一份）",
      "_clamp_to_desktop" in open(
          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "bubble_layout.py"), encoding="utf-8").read()
      and "_check_edge_snap" in open(
          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "bubble_layout.py"), encoding="utf-8").read())

# ---------- 行折叠：宽度/列数变化也要在动画里完成，末尾不许跳 ----------
# 回归（用户报"计算器展开收起时最后一刻跳一下"）：收起一个大组件可能让两列缩成
# 一列，但 _apply_size 用的是 setFixedWidth()（min=max 锁死），折叠动画里的
# setGeometry 改不动宽度；等动画结束、下一次定时刷新再 setFixedWidth 才一帧切
# 过去 —— 实测宽度从 630 一帧跳到 315，位置跟着跳 83~102px。
_wfb = StatusBubbleLayout(FakePet2())
_wfb.pet.resize(75, 75)
_wfb._refresh = lambda *a, **k: None
for _t in ("_timer", "_mv_timer"):
    _tm = getattr(_wfb, _t, None)
    if _tm is not None:
        _tm.stop()
_wcalc, _werr = load_module_widget("calc")
check("行折叠宽度：计算器组件能加载（%s）" % (_werr or "ok"), _wcalc is not None)
if _wcalc is not None:
    # 12 个文字行 + 计算器：收起计算器时列数会从 2 变 1
    _wfb._disp_rows = ([("模块%d" % i, "%d" % i, None) for i in range(12)]
                       + [("计算器", "", _wcalc)])
    _wfb._relayout()
    _wfb.show()
    app.processEvents()
    _w_before = _wfb.width()
    _cols_before = _wfb._ncols
    _wrapc = _wfb._wrap_of(_wcalc)
    _wseq = []
    _wrapc.fold_btn.click()
    _dl = time.time() + 2
    while time.time() < _dl and getattr(_wfb, "_rowfold", None) is not None:
        app.processEvents()
        time.sleep(0.004)
        _g = _wfb.geometry()
        if not _wseq or _wseq[-1] != (_g.x(), _g.width()):
            _wseq.append((_g.x(), _g.width()))
    app.processEvents()
    _w_end = _wfb.width()
    # 动画停下之后，按布局重算一次该多宽 —— 和末帧不一致就是"最后一刻跳一下"
    _wfb._apply_size(_wfb._FIX_W)
    app.processEvents()
    check("行折叠宽度：收起让列数真的变少了（%d 列 -> %d 列）"
          % (_cols_before, _wfb._ncols), _wfb._ncols < _cols_before)
    check("行折叠宽度：动画结束时宽度已是终局，刷新不会再切一次（%d -> %d）"
          % (_w_end, _wfb.width()), abs(_wfb.width() - _w_end) <= 2)
    check("行折叠宽度：宽度是在动画里逐步变的，不是一帧切（宽度取值 %s）"
          % (sorted({w for _x, w in _wseq}),),
          len({w for _x, w in _wseq}) >= 2)
    _wsteps = [abs(_wseq[i + 1][0] - _wseq[i][0])
               for i in range(len(_wseq) - 1)]
    check("行折叠宽度：横向位置也在动画里跟着走，单帧位移不超过 40px（最大 %d）"
          % (max(_wsteps) if _wsteps else 0),
          not _wsteps or max(_wsteps) <= 40)
    check("行折叠宽度：多余的列被收起来了，不残留在画面上",
          all(not _c.isVisible() for _c in _wfb._columns[_wfb._ncols:]))
_wfb.close()

# ---------- 环绕桌宠：把桌宠嵌进短列下方的凹口，整体占地最小 ----------
# 用户要求"把桌宠和气泡看成一个整体，以整体面积最小为目标排列"。分列之后各列
# 高度不齐，靠桌宠那侧的列下方就是一块空缺，而且 _apply_col_mask 早就把它从
# 窗口遮罩里挖掉了——桌宠摆进去照样看得见点得到。
from PyQt5.QtCore import QRect as _QRect2                      # noqa: E402

_nb = StatusBubbleLayout(FakePet2())
_nb.pet.resize(136, 136)
_nb._refresh = lambda *a, **k: None
for _t in ("_timer", "_mv_timer"):
    _tm = getattr(_nb, _t, None)
    if _tm is not None:
        _tm.stop()
_COL = _nb._FIX_W
_SCR = _QRect2(0, 0, 1920, 1080)


def _nook_case(col_hs, pet_x, pet_y, place_left):
    """喂一组列高，返回 (是否嵌入, 整体宽, 并排需要的宽, 是否压住桌宠)。"""
    _nb._col_panels = [(i * _COL, 0, _COL, h) for i, h in enumerate(col_hs)]
    _nb._ncols = len(col_hs)
    _w = _COL * len(col_hs)
    _hh = max(col_hs)
    _nb.resize(_w, _hh)
    _nb.pet.move(pet_x, pet_y)
    _pc = QPoint(pet_x + _nb.pet.width() // 2, pet_y)
    _clear = _nb.pet.width() // 2
    _dock = _nb._nook_dock(_hh, _pc, _SCR, _w, _clear, place_left)
    _pet_r = _QRect2(pet_x, pet_y, _nb.pet.width(), _nb.pet.height())
    if _dock is None:
        _x = (max(_SCR.left(), _pc.x() - _clear - _w - 8) if place_left
              else min(_SCR.right() - _w, _pc.x() + _clear + 8))
        _y = max(_SCR.top(), min(_pc.y() + _nb.pet.height() // 2 - _hh // 2,
                                 _SCR.bottom() - _hh))
    else:
        _x, _y = _dock
    _bub_r = _QRect2(_x, _y, _w, _hh)
    _solid = any(_QRect2(_x + i * _COL, _y, _COL, ch).intersects(_pet_r)
                 for i, ch in enumerate(col_hs))
    return (_dock is not None, _bub_r.united(_pet_r).width(),
            _w + _nb.pet.width(), _solid)

# 凹口够深 + 桌宠在下半屏：应当嵌进去，整体宽度省掉一整列
for _nm, _hs, _px, _py, _left in (("气泡在左", [700, 380], 1500, 700, True),
                                  ("气泡在右", [380, 700], 200, 700, False),
                                  ("桌宠靠底", [700, 300], 1500, 900, True),
                                  ("三列最右最短", [700, 700, 300], 1500, 700, True)):
    _docked, _uw, _side_w, _solid = _nook_case(_hs, _px, _py, _left)
    # 嵌进去之后桌宠横向完全落在气泡的跨度里，整体宽度就等于气泡自己的宽度，
    # 比"并排摆"整整省下一个桌宠的身宽
    check("环绕桌宠(%s)：桌宠横向嵌进气泡跨度，整体宽度省掉一个桌宠身宽"
          "（并排 %d -> 环绕 %d）" % (_nm, _side_w, _uw),
          _docked and _uw <= _side_w - _nb.pet.width() + 8)
    check("环绕桌宠(%s)：桌宠没被气泡的实心列压住" % _nm, not _solid)

# 凹口不够 / 伸不开：必须老实退回并排，不能硬塞
for _nm, _hs, _px, _py, _left in (("凹口太浅", [700, 660], 1500, 700, True),
                                  ("桌宠偏上伸不开", [700, 380], 1500, 300, True),
                                  ("单列", [700], 1500, 700, True)):
    _docked, _uw, _side_w, _solid = _nook_case(_hs, _px, _py, _left)
    check("环绕桌宠(%s)：放不下就退回并排，不硬塞" % _nm, not _docked)
    check("环绕桌宠(%s)：退回并排时也没压住桌宠" % _nm, not _solid)
_nb.close()

# ---------- 「气泡保持」开着时，「关闭气泡」也必须管用 ----------
# 回归：hide_animated() 一看到 _pinned 就 return，于是开了气泡保持之后点
# 关闭气泡毫无反应（用户反馈）。关闭是更强的意图，应当压过保持状态。
_pet.recenter()
app.processEvents()
_bub._do_show()
app.processEvents()
_pet._toggle_bubble_keep()                 # = 气泡保持（钉住）
app.processEvents()
check("气泡保持：打开后气泡是钉住态", _bub._pinned)
check("气泡保持：钉住时单独调 hide_animated 确实不该隐藏（原有行为）",
      (_bub.hide_animated() or True) and _bub._pinned)
_pet._toggle_bubble()                       # = 关闭气泡
app.processEvents()
_dl = time.time() + 2
while time.time() < _dl and _bub.isVisible():
    app.processEvents()
    time.sleep(0.02)
check("气泡保持中点「关闭气泡」：钉住被解除", not _bub._pinned)
check("气泡保持中点「关闭气泡」：气泡真的收起来了", not _bub.isVisible())
check("气泡保持中点「关闭气泡」：钉住按钮的图标也跟着复位",
      _bub._pin_btn.text() == "○")
check("关闭气泡后设置里记下了", _pet.settings.get("bubble_enabled") is False)
_pet._toggle_bubble()                       # 再打开
app.processEvents()
check("再点「打开气泡」：开关状态恢复",
      _pet.bubble_enabled and _pet.settings.get("bubble_enabled") is True)

try:
    _pet.close()
except Exception:
    pass
app.processEvents()

data_store.DATA_DIR = _ds_dir_bak
import shutil as _sh                                          # noqa: E402
_sh.rmtree(_fold_sand, ignore_errors=True)

# ---------- 失败原因直接写在值区 ----------
# 以前失败只显示"获取失败"，真正原因藏在标题的悬停提示里——得把鼠标停上去才知道
# 是自己断网了还是接口挂了。
_cases = [("HTTP Error 503: Service Unavailable", "服务端错误"),
          ("<urlopen error timed out>", "超时"),
          ("urlopen error [Errno 11001] getaddrinfo failed", "没网"),
          ("HTTP Error 401: Unauthorized", "Key 无效"),
          ("HTTP Error 429: Too Many Requests", "太频繁"),
          ("SSLCertVerificationError: certificate verify failed", "证书错误"),
          ("HTTP Error 404: Not Found", "地址不存在"),
          ("JSONDecodeError: Expecting value", "返回格式不对"),
          ("HTTP Error 418: I'm a teapot", "418")]
_wrong = [(m, bubble_layout.short_error(m), w) for m, w in _cases
          if bubble_layout.short_error(m) != w]
if _wrong:
    print("  没对上的映射:", _wrong[:3])
check("失败原因：常见报错都能压成一句短话", not _wrong)
check("失败原因：认不出来的报错不瞎猜（返回空，保持原值）",
      bubble_layout.short_error("某种没见过的错") == ""
      and bubble_layout.short_error("") == "")
_er = _LTextRow()
_er.set_content("天气", "获取失败", 120,
                state={"kind": "error", "error": "HTTP Error 503"})
check("失败原因：值区显示原因，不再只写「获取失败」",
      _er.value._text == "服务端错误")
check("失败原因：完整报错仍保留在标题悬停提示里", "503" in _er.title.toolTip())
_er.set_content("天气", "获取失败", 120,
                state={"kind": "error", "error": "timed out"})
check("失败原因：原因变了值区跟着变（签名带上了它，不会被增量刷新跳过）",
      _er.value._text == "超时")
_er.set_content("天气", "26°C", 120, state={"kind": "ok"})
check("失败原因：恢复正常后显示真实值", _er.value._text == "26°C")

# ---------- 汇总 ----------
print("\n==== %d passed, %d failed ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
