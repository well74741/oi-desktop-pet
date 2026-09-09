# -*- coding: utf-8 -*-
"""快捷启动器组件：一排按钮，点击打开程序/网址/文件/文件夹。

规则配置里 source.ui 填 "launcher" 即可。
条目存 launcher_data.json：{ 模块名: [{label, path}, ...] }
默认含常用项，可在 JSON 里增删。
"""
import os
import subprocess
import sys
import webbrowser

from PyQt5.QtCore import Qt

import data_store
from widgets import ModuleWidget, kit

_DEFAULTS = [
    {"label": "记事本", "path": "notepad"},
    {"label": "计算器", "path": "calc"},
    {"label": "浏览器", "path": "https://www.baidu.com"},
    {"label": "此电脑", "path": "explorer"},
]


class Widget(ModuleWidget):
    """快捷启动器：点击按钮打开对应程序/网址/文件。"""

    FIX_H = 66

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._lay = kit.col(spacing=2, margins=(4, 3, 4, 3))
        self.setLayout(self._lay)
        self._load()
        self._rebuild()

    def _key(self):
        return str(self.rule.get("name", "快捷启动") or "快捷启动")

    def _load(self):
        try:
            d = data_store.read_dict("launcher_data.json")
            items = d.get(self._key())
        except Exception:
            items = None
        self._items = items if isinstance(items, list) and items else list(_DEFAULTS)

    def _rebuild(self):
        # 清空旧按钮行
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        # 两行布局
        row = kit.row(spacing=4)
        for i, it in enumerate(self._items):
            label = str(it.get("label", "?"))
            b = kit.btn(label, small=True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(str(it.get("path", "")))
            b.clicked.connect(lambda _=False, p=it.get("path", ""): self._open(p))
            row.addWidget(b)
            if (i + 1) % 4 == 0:
                self._lay.addLayout(row)
                row = kit.row(spacing=4)
        if row.count():
            row.addStretch(1)
            self._lay.addLayout(row)
        self._lay.addStretch(1)

    def _open(self, path):
        try:
            p = str(path or "").strip()
            if not p:
                return
            if p.startswith(("http://", "https://")):
                webbrowser.open(p)
            elif p.lower().endswith((".exe", ".bat", ".cmd")):
                subprocess.Popen([p], shell=True)
            elif os.path.isdir(p):
                subprocess.Popen(["explorer", p])
            elif os.path.exists(p):
                os.startfile(p) if sys.platform == "win32" else subprocess.Popen([p])
            else:
                # 系统命令（notepad/calc 等）
                subprocess.Popen([p], shell=True)
        except Exception:
            pass

    def render(self, state, value):
        pass
