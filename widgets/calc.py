# -*- coding: utf-8 -*-
"""计算器组件：气泡内简单四则运算（+ - × ÷ 括号），无持久化。

规则配置里 source.ui 填 "calc" 即可。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGridLayout, QLineEdit

from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """计算器：数字/运算符输入，= 求值，C 清空。"""

    FIX_H = 210

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expr = ""

        self._display = QLineEdit(self)
        self._display.setReadOnly(True)
        self._display.setAlignment(Qt.AlignRight)
        self._display.setFixedHeight(kit.bs(28))
        self._display.setStyleSheet(kit.scale_qss(
            "QLineEdit{background:rgba(255,255,255,22);border:1px solid "
            "rgba(255,255,255,45);border-radius:4px;color:#e8ecf5;"
            "font-family:SimHei;font-size:14px;padding:2px 6px;}"))

        grid = QGridLayout()
        grid.setSpacing(kit.bs(2))
        keys = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2), ("÷", 0, 3),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2), ("×", 1, 3),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2), ("-", 2, 3),
            ("0", 3, 0), (".", 3, 1), ("+", 3, 2), ("=", 3, 3),
            ("C", 4, 0), ("(", 4, 1), (")", 4, 2), ("⌫", 4, 3),
        ]
        self._btns = {}
        for text, r, c in keys:
            b = kit.btn(text, primary=(text == "="), small=True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=text: self._press(t))
            grid.addWidget(b, r, c)
            self._btns[text] = b

        lay = kit.col(self._display, grid,
                      spacing=3, margins=(4, 2, 4, 3))
        self.setLayout(lay)

    def _press(self, t):
        if t == "C":
            self._expr = ""
        elif t == "⌫":
            self._expr = self._expr[:-1]
        elif t == "=":
            try:
                expr = (self._expr.replace("×", "*")
                        .replace("÷", "/").replace("^", "**"))
                if not expr.strip():
                    return
                # 安全求值：仅允许数字与运算符
                import re as _re
                if _re.fullmatch(r"[0-9+\-*/().\s]*", expr):
                    val = eval(expr, {"__builtins__": {}}, {})
                    self._expr = str(val)
                else:
                    self._expr = "错误"
            except Exception:
                self._expr = "错误"
        else:
            self._expr += t
        self._display.setText(self._expr)

    def render(self, state, value):
        pass
