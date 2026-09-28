# -*- coding: utf-8 -*-
"""待办清单组件：输入框 + 列表 + 添加/删除/清空 + 收起/展开，任务本地持久化。

规则配置里 source.ui 填 "todo" 即可在气泡/测试区显示。
任务保存在项目根目录的 todo_data.json，重启桌宠不丢失。
收起后只显示一行标题（待办数量 + 展开按钮），方便收纳列表。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget,
                             QPushButton, QVBoxLayout)

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """待办清单：添加 / 删除选中 / 清空 / 收起，回车快速添加。"""

    FIX_H = 172
    _COLLAPSED_H = 30

    _QSS = (
        "QLineEdit{background:rgba(255,255,255,26);border:1px solid rgba(255,255,255,55);"
        "border-radius:4px;color:#e8ecf5;font-family:Microsoft YaHei;font-size:10px;padding:2px 6px;}"
        "QListWidget{background:rgba(255,255,255,18);border:1px solid rgba(255,255,255,45);"
        "border-radius:4px;color:#e8ecf5;font-family:Microsoft YaHei;font-size:10px;}"
        "QListWidget::item{padding:2px 4px;color:#e8ecf5;}"
        "QListWidget::item:hover{background:rgba(255,255,255,30);color:#ffffff;}"
        "QListWidget::item:selected{background:rgba(74,144,226,120);color:#ffffff;}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self.setFixedHeight(self.current_height())
        self.setStyleSheet(kit.scale_qss(self._QSS))
        self.setStyleSheet(self.styleSheet() + "\n" + kit.action_qss(False))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, kit.bs(4))   # 底部留白，按钮不贴模块边框
        lay.setSpacing(kit.bs(4))

        self._row1 = QHBoxLayout()
        self._row1.setSpacing(kit.bs(4))
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("输入任务，回车添加")
        self._input.setFont(kit.font_pt(9))
        self._input.returnPressed.connect(self._add)
        self._row1.addWidget(self._input, 1)
        self._add_btn = QPushButton("添加", self)
        self._add_btn.clicked.connect(self._add)
        self._row1.addWidget(self._add_btn, 0, Qt.AlignVCenter)
        lay.addLayout(self._row1)

        self._list = QListWidget(self)
        self._list.setFont(kit.font_pt(9))
        lay.addWidget(self._list, 1)

        row2 = QHBoxLayout()
        row2.setSpacing(kit.bs(4))
        self._del_btn = QPushButton("删除选中", self)
        self._del_btn.clicked.connect(self._del)
        row2.addWidget(self._del_btn, 0, Qt.AlignVCenter)
        self._clear_btn = QPushButton("清空", self)
        self._clear_btn.clicked.connect(self._clear)
        row2.addWidget(self._clear_btn, 0, Qt.AlignVCenter)
        row2.addStretch()
        self._fold_btn = QPushButton("收起", self)
        self._fold_btn.clicked.connect(self._toggle_fold)
        row2.addWidget(self._fold_btn, 0, Qt.AlignVCenter)
        self._row2 = row2
        lay.addLayout(row2)

        self._load()

    # ---- 高度自适应（框架用 current_height 而非 FIX_H 时支持收起） ----
    def current_height(self):
        return (kit.bs(self._COLLAPSED_H) if self._collapsed
                else kit.bs(self.FIX_H))

    def _toggle_fold(self):
        self._collapsed = not self._collapsed
        self._fold_btn.setText("展开" if self._collapsed else "收起")
        for w in (self._input, self._add_btn, self._list,
                  self._del_btn, self._clear_btn):
            w.setVisible(not self._collapsed)
        self.setFixedHeight(self.current_height())
        if getattr(self, "on_resize", None):
            try:
                self.on_resize()
            except Exception:
                pass

    # ---- 框架回调 ----
    def render(self, state, value):
        """脚本取数结果无需参与：任务列表由组件自身管理。"""
        pass

    # ---- 数据持久化 ----
    def reload(self):
        """重新读取 todo_data.json 并刷新列表（AI 工具改文件后由框架调用）。"""
        try:
            self._list.clear()
            self._load()
        except Exception:
            pass

    def _load(self):
        try:
            for t in data_store.read_list("todo_data.json"):
                self._list.addItem(str(t))
        except Exception:
            pass

    def _save(self):
        """组件列表为准直接写文件。AI 工具改文件后框架会调 reload()
        刷新本组件，所以不会出现"组件旧列表覆盖 AI 新加项"的情况。"""
        try:
            tasks = [self._list.item(i).text()
                     for i in range(self._list.count())]
            data_store.write_json("todo_data.json", tasks)
        except Exception:
            pass

    # ---- 交互 ----
    def _add(self):
        t = self._input.text().strip()
        if not t:
            return
        self._list.addItem(t)
        self._input.clear()
        self._save()

    def _del(self):
        row = self._list.currentRow()
        if row < 0:
            return
        self._list.takeItem(row)
        self._save()

    def _clear(self):
        if self._list.count() == 0:
            return
        if kit.confirm(self, "清空任务", "确定清空所有任务？", danger=True):
            self._list.clear()
            self._save()
