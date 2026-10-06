# -*- coding: utf-8 -*-
"""计数器组件：一个按钮 +1 / 清零，计数按模块名持久化到 counter_data.json。

规则配置里 source.ui 填 "counter" 即可（AI 助手 add_module 可直接引用），
例如"生气计数器"：{"name":"生气计数器","source":{"type":"script","lang":"python",
"ui":"counter","code":"result = '计数器'"},"interval":3600}
"""
from PyQt5.QtCore import Qt

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """计数组件：点击 +1，右键/清零按钮归零。"""

    FIX_H = 117

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0

        self._label = kit.lab("0", size=24, bold=True,
                              align=Qt.AlignCenter, wrap=False)
        btn = kit.btn("＋1", primary=True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self._inc)
        reset = kit.btn("清零", small=True)
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset)

        lay = kit.col(
            kit.row(btn, reset),
            self._label,
            spacing=4.5, margins=(0, 6, 0, 7.5))   # 横向边距由模块卡片统一控制
        self.setLayout(lay)
        self._load()

    def set_rule(self, rule):
        super().set_rule(rule)
        self._load()

    # ---- 持久化 ----
    def _key(self):
        return str(self.rule.get("name", "计数器") or "计数器")

    def _load(self):
        try:
            data = data_store.read_dict("counter_data.json")
            self._count = int(data.get(self._key(), 0) or 0)
        except Exception:
            self._count = 0
        self._update_label()

    def _save(self):
        try:
            def _mut(d):
                d[self._key()] = self._count
                return d
            data_store.mutate_json("counter_data.json", {}, _mut)
        except Exception:
            pass

    def _update_label(self):
        self._label.setText("计数 %d" % self._count)

    # ---- 交互 ----
    def _inc(self):
        self._count += 1
        self._update_label()
        self._save()

    def _reset(self):
        self._count = 0
        self._update_label()
        self._save()

    def render(self, state, value):
        pass
