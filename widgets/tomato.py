# -*- coding: utf-8 -*-
"""示例自定义组件：带开始/暂停/重置按钮的番茄时钟（Qt 组件，主线程运行）。

规则配置里 source.ui 填 "tomato" 即可在气泡/测试区显示。
与内置 pomodoro 卡片不同：代码完全在这个文件里，可自由修改样式与交互。
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from widgets import ModuleWidget
from widgets import kit


class Widget(ModuleWidget):
    """番茄时钟：工作 25 分钟 / 休息 5 分钟，倒计时 + 三个按钮。"""

    FIX_H = 78
    _QSS = (
        "QLabel{color:#e8ecf5;font-family:SimHei;font-size:11px;}"
    )

    def current_height(self):
        """展开高度按气泡档位缩放。"""
        return kit.bs(self.FIX_H)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state.setdefault("mode", "work")
        self.state.setdefault("remaining", 25 * 60)
        self.state.setdefault("running", False)
        self.state.setdefault("last", time.monotonic())
        self.state.setdefault("work", 25 * 60)
        self.state.setdefault("break", 5 * 60)
        self.setFixedHeight(self.current_height())
        self.setStyleSheet(kit.scale_qss(self._QSS))
        self.setStyleSheet(self.styleSheet() + "\n" + kit.action_qss(False))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(kit.bs(4), kit.bs(2),
                               kit.bs(4), kit.bs(4))   # 底部留白，按钮不贴卡片下框
        lay.setSpacing(kit.bs(2))
        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignCenter)
        f = kit.font_pt(16)
        self.time_label.setFont(f)
        lay.addWidget(self.time_label)

        btns = QHBoxLayout()
        btns.setSpacing(kit.bs(4))
        self.start_btn = QPushButton("开始")
        self.pause_btn = QPushButton("暂停")
        self.reset_btn = QPushButton("重置")
        self.start_btn.clicked.connect(self._start)
        self.pause_btn.clicked.connect(self._pause)
        self.reset_btn.clicked.connect(self._reset)
        for b in (self.start_btn, self.pause_btn, self.reset_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(200)
        self._sync()

    # ---- 框架回调 ----
    def render(self, state, value):
        """后台规则取数完成后调用（value 为脚本输出，可自行合并显示）。"""
        self._sync()

    # ---- 内部逻辑（主线程） ----
    def _start(self):
        if not self.state.get("running"):
            self.state["last"] = time.monotonic()
            self.state["running"] = True

    def _pause(self):
        self._tick()
        self.state["running"] = False

    def _reset(self):
        self.state["running"] = False
        self.state["mode"] = "work"
        self.state["remaining"] = float(self.state.get("work", 25 * 60))
        self._sync()

    def _tick(self):
        if self.state.get("running"):
            now = time.monotonic()
            self.state["remaining"] = max(
                0.0, self.state["remaining"] - (now - self.state["last"]))
            self.state["last"] = now
            if self.state["remaining"] <= 0:
                self._switch()
        self._sync()

    def _switch(self):
        if self.state.get("mode") == "work":
            self.state["mode"] = "break"
            self.state["remaining"] = float(self.state.get("break", 5 * 60))
        else:
            self.state["mode"] = "work"
            self.state["remaining"] = float(self.state.get("work", 25 * 60))
        self.state["running"] = False

    def _sync(self):
        m, s = divmod(max(0, int(round(self.state.get("remaining", 0)))), 60)
        label = "专注" if self.state.get("mode") == "work" else "休息"
        self.time_label.setText("%s %02d:%02d" % (label, m, s))
