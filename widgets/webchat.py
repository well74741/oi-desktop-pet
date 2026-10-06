# -*- coding: utf-8 -*-
"""聚合 AI 组件：单行布局（标题 + 打开按钮），点一下直接开。

界面逻辑（左侧边栏 / 站点管理）都在顶层的 `webchat_ui.py`：组件是运行期按
文件路径动态加载的，PyInstaller 静态分析看不见这个文件，把界面代码留在这里
会让它的依赖漏打包（v0.9.2 画布/拼豆就是这么坏的）。这里只留一行按钮。

按钮就叫「打开」，不弹站点列表：换模型、加站点、置顶全在聚合AI 窗口左边
那条常驻侧边栏上，手在浏览器上时改比回桌宠点菜单顺手。切站点在同一个窗口里
换页（旧页面藏起来不关），像浏览器标签页，切回去是瞬间的、状态也不丢。

站点用系统浏览器的「应用窗口」模式打开（无地址栏/无标签页），再用 SetParent
塞进桌宠自己的宿主窗口里，于是侧边栏占真实布局空间、网页被挤窄而不是被盖住。
这样网页端的完整能力（联网搜索 / 深度研究 / 文件上传 / 画布）一个不少，而
安装包里不必塞一份浏览器内核。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget

from webchat_ui import (SiteManagerDialog, opener,  # noqa: F401 （对外转发）
                        open_webchat)
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """气泡组件：一行 = 模块名 + 打开按钮。"""

    FIX_H = 22.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = "聚合AI"

        self._name = kit.lab(self._label, size=10.5, color="#a8e6a3", bold=True)
        self._open_btn = kit.btn("打开", primary=True, small=True)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._on_click)

        spacer = QWidget()
        spacer.setFixedWidth(0)   # 弹性占位（配合 setStretch），0 宽即可
        # 横向边距交给模块卡片统一控制（卡片已有内边距），组件内部不再另加，
        # 否则按钮会比文本行的值多缩进一截，出现"按钮没贴边/右边缘不齐"。
        lay = kit.row(self._name, spacer, self._open_btn,
                      spacing=6, margins=(0, 0, 0, 0))
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
    def _on_click(self):
        self._open_btn.setEnabled(False)
        self._open_btn.setText("打开中")
        if not open_webchat(self, finished=self._on_opened):
            self._on_opened(True, "")   # 没开成（没浏览器/正开着）：复原按钮

    def _on_opened(self, ok, err):
        self._open_btn.setEnabled(True)
        self._open_btn.setText("打开")
        if not ok and err:
            kit.warn(self, "聚合AI", err)

    def render(self, state, value):
        pass
