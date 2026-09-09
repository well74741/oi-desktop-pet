# -*- coding: utf-8 -*-
"""聚合 AI 组件：单行布局（标题 + 打开按钮），点开弹出聚合 AI 聊天悬浮窗口。"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox, QWidget

from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """气泡组件：一行 = 模块名 + 打开按钮。"""

    FIX_H = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = "聚合AI"

        self._name = kit.lab(self._label, size=7, color="#a8e6a3", bold=True)
        self._open_btn = kit.btn("打开", primary=True, small=True)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._open_panel)

        spacer = QWidget()
        spacer.setFixedWidth(0)   # 弹性占位（配合 setStretch），0 宽即可
        lay = kit.row(self._name, spacer, self._open_btn,
                      spacing=4, margins=(4, 0, 4, 0))
        lay.setStretch(1, 1)   # spacer 弹性占位，把按钮顶到右边
        self.setLayout(lay)

    # ---------- 规则注入 ----------
    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._label = name
        if getattr(self, "_name", None):
            self._name.setText(self._label)

    # ---------- 打开 ----------
    def _open_panel(self):
        try:
            import webchat_panel
            webchat_panel.show_panel()
        except Exception as e:
            QMessageBox.warning(self, "聚合AI", "打开失败：%s" % e)

    def render(self, state, value):
        pass
