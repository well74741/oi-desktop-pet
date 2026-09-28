# -*- coding: utf-8 -*-
"""量一量 AI 对话面板（ChatPanel）收起 / 展开时各块的实际几何，找裁切和错位。

用法：python _check_chatpanel.py [气泡档位]
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout

app = QApplication([])

from widgets import kit                      # noqa: E402

scale = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
kit.set_bubble_scale(scale)

import bubble_ui                             # noqa: E402

GREET = ("你好！我是你的 AI 助手，可以：管理待办/模块/按钮、控制桌宠、"
         "调整桌宠设置、查时间、开网页、设提醒。")


def dump(tag, p):
    print("\n==== %s ====" % tag)
    print("面板 %sx%s   current_height=%s   _base_h=%s"
          % (p.width(), p.height(), p.current_height(), p._base_h()))
    rows = [("header", getattr(p, "_header_widget", None)),
            ("history", p.history), ("collapsed_label", p.collapsed_label),
            ("input", p.input), ("up_btn", p.up_btn), ("down_btn", p.down_btn),
            ("fold_btn", p.fold_btn), ("trash_btn", p.trash_btn)]
    geo = {}
    for name, w in rows:
        if w is None:
            continue
        g = w.geometry()
        # 子控件可能在 _bottom 包装层里，换算到面板坐标再判断有没有被裁
        tl = w.mapTo(p, w.rect().topLeft())
        g = QRect(tl, g.size())
        geo[name] = g
        vis = w.isVisibleTo(p)
        cut = ""
        if vis and (g.bottom() > p.height() - 1 or g.right() > p.width() - 1
                    or g.top() < 0 or g.left() < 0):
            cut = "  <<< 超出面板，被裁切"
        print("  %-16s vis=%-5s  x=%-4d y=%-4d w=%-4d h=%-4d  bottom=%-4d%s"
              % (name, vis, g.x(), g.y(), g.width(), g.height(), g.bottom(), cut))
    iu, ib = geo["input"].top(), geo["input"].bottom()
    gu = min(geo["up_btn"].top(), geo["fold_btn"].top())
    gb = max(geo["down_btn"].bottom(), geo["trash_btn"].bottom())
    print("  输入框 top/bottom = %d/%d ；按钮列 top/bottom = %d/%d" % (iu, ib, gu, gb))
    print("  底端差 %+d  %s" % (gb - ib,
                                "（贴输入框底边对齐）" if abs(gb - ib) <= 2
                                else "<<< 没对齐"))
    print("  收起态标签文本: %r" % p.collapsed_label.text())


host = QWidget()
host.resize(kit.bs(210), 400)
hl = QVBoxLayout(host)
hl.setContentsMargins(0, 0, 0, 0)
p = bubble_ui.ChatPanel(host)
hl.addWidget(p)
p.show()
host.show()
p._messages = [{"role": "assistant", "text": GREET}]
p.rebuild()
app.processEvents()

# 展开态：按 height_hint 给高度（外层就是这么摆的）
p.resize(kit.bs(210), p.current_height()); host.resize(kit.bs(210), p.current_height()); app.processEvents()
app.processEvents()
dump("展开", p)

# 收起态：跳过动画直接切状态，和 _finish_fold 的终局一致
p._collapsed = True
p.fold_btn.set_down(False)
p._finish_fold()
app.processEvents()
p.resize(kit.bs(210), p.current_height()); host.resize(kit.bs(210), p.current_height()); app.processEvents()
app.processEvents()
dump("收起", p)

# 收起态 + 输入框里打了几行字：current_height 还认死 _BASE_H 的话就会裁掉
p.input.setPlainText("第一行\n第二行\n第三行")
app.processEvents()
p.resize(kit.bs(210), p.current_height()); host.resize(kit.bs(210), p.current_height()); app.processEvents()
app.processEvents()
dump("收起 + 输入三行", p)
p.input.setPlainText("")
app.processEvents()

# 带标题栏（AI 助手实际是有的）
p.set_header("AI助手", right_text="deepseek-chat")
app.processEvents()
p.resize(kit.bs(210), p.current_height()); host.resize(kit.bs(210), p.current_height()); app.processEvents()
app.processEvents()
print("\n带标题栏：current_height=%d，标题栏高=%d，标题栏算进去了吗 -> %s"
      % (p.current_height(),
         p._header_widget.height() if getattr(p, "_header_widget", None) else 0,
         "没有（外层另加）" if p.current_height() < p.height() else "看下面"))
dump("收起 + 标题栏", p)
