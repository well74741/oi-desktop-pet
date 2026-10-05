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

    @staticmethod
    def _safe_cwd(path=""):
        """被启动的程序用哪个工作目录 —— **不能是桌宠的安装目录**。

        子进程继承 CWD，而 Windows 的 DLL 搜索顺序会查当前目录：被启动的程序
        可能从桌宠的 _internal 目录里加载 VCRUNTIME140.dll 这类公共运行时并一直
        占着文件句柄，导致装新版时替换不了那个 DLL（实测占用者是从桌宠启动的
        Photoshop，而不是桌宠自己）。和 pet_gravity._launch_cwd 是同一件事。
        """
        try:
            q = str(path or "")
            if q and os.path.isfile(q):
                d = os.path.dirname(os.path.abspath(q))
                if d and os.path.isdir(d):
                    return d
            home = os.path.expanduser("~")
            return home if os.path.isdir(home) else None
        except Exception:
            return None

    def _open(self, path):
        try:
            p = str(path or "").strip()
            if not p:
                return
            # 已经开着的程序切过去（和径向菜单同一套规则，见 app_focus.try_focus）
            try:
                import app_focus
                import pet_gravity
                if (pet_gravity.load_settings().get("launch_focus_existing", True)
                        and app_focus.try_focus(p, pet_gravity._resolve_lnk_target)):
                    return
            except Exception:
                pass
            cwd = self._safe_cwd(p)
            if p.startswith(("http://", "https://")):
                webbrowser.open(p)
            elif p.lower().endswith((".exe", ".bat", ".cmd")):
                subprocess.Popen([p], shell=True, cwd=cwd)
            elif os.path.isdir(p):
                subprocess.Popen(["explorer", p], cwd=cwd)
            elif os.path.exists(p):
                if sys.platform == "win32":
                    _old = os.getcwd()
                    try:
                        if cwd:
                            os.chdir(cwd)
                        os.startfile(p)
                    finally:
                        try:
                            os.chdir(_old)
                        except Exception:
                            pass
                else:
                    subprocess.Popen([p], cwd=cwd)
            else:
                # 系统命令（notepad/calc 等）
                subprocess.Popen([p], shell=True, cwd=cwd)
        except Exception:
            pass

    def render(self, state, value):
        pass
