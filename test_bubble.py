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
        self.relayouts = 0

    def _current_side(self):
        return "above"

    def _fold_set_height(self, panel, wh):
        self.wrap_heights.append(int(wh))

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

p.toggle_collapse()
dl = time.time() + 1
while time.time() < dl and p._fold_anim is not None:
    app.processEvents()
    time.sleep(0.02)
app.processEvents()
check("收起动画完成", p._collapsed and not b._fold_locked
      and len(b.wrap_heights) > 5 and b.wrap_heights[-1] <= b.wrap_heights[0])
check("折叠按钮朝上", not p.fold_btn._down)

p.toggle_collapse()
dl = time.time() + 1
while time.time() < dl and p._fold_anim is not None:
    app.processEvents()
    time.sleep(0.02)
app.processEvents()
check("展开动画完成", not p._collapsed and not b._fold_locked)
check("折叠按钮朝下", p.fold_btn._down)

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
      row._last_content == ("CPU", "60%", False, "", "ok"))

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
    FIX_H = 158


kit.set_bubble_scale(2.0)
fixed = FixedModule()
# 期望值 = 标准档像素 × 档位 × 界面基准倍率（kit.UI_BASE，默认 1.5）
def _x(v, lvl):
    return max(1, int(round(v * lvl * kit.UI_BASE)))


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
        panel.resize(kit.bs(210), h)
        _host.resize(kit.bs(210), h)
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
    need = bubble_layout._font_h() + 2 * kit.bs(1)
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
_cb.close()

# ---------- 汇总 ----------
print("\n==== %d passed, %d failed ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
