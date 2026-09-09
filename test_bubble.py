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
from PyQt5.QtWidgets import QApplication, QWidget

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
check("FIX_H 框架统一放大", _widget_height(fixed, 420) == 316)

kit.set_bubble_scale(1.0)
bub1 = StatusBubbleLayout(FakePet2())
bub1._disp_rows = [("CPU", "50%", None)]
bub1._relayout()
check("标准气泡宽度", bub1.width() == 210)
check("标准行高不裁切",
      bub1._row_widgets[0].minimumHeight() >= 15)

kit.set_bubble_scale(2.0)
bub2 = StatusBubbleLayout(FakePet2())
bub2._disp_rows = [("CPU", "50%", None)]
bub2._relayout()
check("大气泡宽度", bub2.width() == 420)
check("大气泡行高等比下限",
      bub2._row_widgets[0].minimumHeight() >= 30)
check("气泡设计令牌统一缩放",
      kit.bubble_token("width") == 420
      and kit.bubble_token("title_width") == 88
      and kit.bubble_token("action_height") == 26)
kit.set_bubble_scale(1.0)

# ---------- 汇总 ----------
print("\n==== %d passed, %d failed ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
