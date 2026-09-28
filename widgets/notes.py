# -*- coding: utf-8 -*-
"""便签/记事本组件：气泡内多行文字输入，自动保存，按模块名持久化。

规则配置里 source.ui 填 "notes" 即可。
数据存 notes_data.json：{ 模块名: 文本 }
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QPlainTextEdit

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """便签组件：输入即存，可清空。"""

    FIX_H = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""

        self._edit = QPlainTextEdit(self)
        self._edit.setPlaceholderText("记点什么…（自动保存）")
        self._edit.setFont(kit.font_pt(9))
        self._edit.setStyleSheet(kit.scale_qss(
            "QPlainTextEdit{background:rgba(255,255,255,22);border:1px solid "
            "rgba(255,255,255,45);border-radius:4px;color:#e8ecf5;"
            "padding:4px;font-family:Microsoft YaHei;font-size:10px;}"))
        self._edit.textChanged.connect(self._on_changed)
        clear_btn = kit.btn("清空", small=True)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear)

        lay = kit.col(
            self._edit,
            kit.row(kit.lab("便签", size=7, color="#96a7c4"), kit.hsep(),
                    clear_btn),
            spacing=3, margins=(0, 2, 0, 3))   # 横向边距由模块卡片统一控制
        self.setLayout(lay)
        self._load()

    def set_rule(self, rule):
        super().set_rule(rule)
        self._load()

    def _key(self):
        return str(self.rule.get("name", "便签") or "便签")

    def _load(self):
        try:
            d = data_store.read_dict("notes_data.json")
            self._text = str(d.get(self._key(), "") or "")
        except Exception:
            self._text = ""
        self._edit.setPlainText(self._text)

    def _save(self):
        try:
            def _mut(d):
                d[self._key()] = self._text
                return d
            data_store.mutate_json("notes_data.json", {}, _mut)
        except Exception:
            pass

    def _on_changed(self):
        self._text = self._edit.toPlainText()
        self._save()

    def _clear(self):
        self._edit.clear()
        self._text = ""
        self._save()

    def render(self, state, value):
        pass
