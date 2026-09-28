# -*- coding: utf-8 -*-
"""示例组件：控制面板（演示 kit 工具包 + 常用交互与布局）。

包含：开关切换、动态进度条、按钮、可滚动日志列表。
规则配置里 source.ui 填 "panel" 即可在气泡与测试区显示。
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout

from widgets import kit
from widgets import ModuleWidget


class Widget(ModuleWidget):
    FIX_H = 158   # 组件固定高度；框架按此排行高

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._log = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(kit.bs(6), kit.bs(4),
                               kit.bs(6), kit.bs(4))
        lay.setSpacing(kit.bs(4))

        head = kit.row()
        self.title = kit.lab("控制面板", size=11, bold=True)
        head.addWidget(self.title)
        head.addStretch()
        self.state_lab = kit.lab("已开启", size=9, color="#7dc98f")
        head.addWidget(self.state_lab)
        lay.addLayout(head)

        bar_row = kit.row()
        self.bar_lab = kit.lab("负载 0%", size=9, color="#9fb0cc", wrap=False)
        bar_row.addWidget(self.bar_lab)
        bar_row.addStretch()
        self.bar = kit.progress(0)
        bar_row.addWidget(self.bar, 1)
        lay.addLayout(bar_row)

        act_row = kit.row()
        self.switch = kit.switch(True)
        self.switch.toggled.connect(self._on_toggle)
        act_row.addWidget(self.switch)
        act_row.addStretch()
        add_btn = kit.btn("记一条", small=True)
        add_btn.clicked.connect(self._add_log)
        act_row.addWidget(add_btn)
        clear_btn = kit.btn("清空", small=True)
        clear_btn.clicked.connect(self._clear_log)
        act_row.addWidget(clear_btn)
        lay.addLayout(act_row)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QListWidget.NoSelection)
        lay.addWidget(kit.scroll(self.listw, max_h=64))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._sync()

    # ---- 框架回调 ----
    def render(self, state, value):
        """后台规则取数完成后调用；这里只做空实现（面板自驱动）。"""
        pass

    # ---- 交互 ----
    def _on_toggle(self, on):
        self.state_lab.setText("已开启" if on else "已关闭")
        self.state_lab.setStyleSheet(
            "color:%s;background:transparent;" % ("#7dc98f" if on else "#d08a8a"))

    def _tick(self):
        self._value = (self._value + 3) % 101
        self.bar.setValue(self._value)
        self.bar_lab.setText("负载 %d%%" % self._value)

    def _add_log(self):
        self._log.append(time.strftime("%H:%M:%S") + " 记录一条")
        self._refresh_log()

    def _clear_log(self):
        self._log.clear()
        self._refresh_log()

    def _refresh_log(self):
        self.listw.clear()
        for line in self._log[-50:]:
            it = QListWidgetItem(line)
            it.setForeground(Qt.white)
            self.listw.addItem(it)
        if not self._log:
            self.listw.addItem(QListWidgetItem("（暂无记录）"))

    def _sync(self):
        self._refresh_log()
