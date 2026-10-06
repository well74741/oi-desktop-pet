# -*- coding: utf-8 -*-
"""拼豆模块：11x10 像素网格，用颜色填充圆角方块。

- 工具行（自适应拉长）：填色笔 / 删除（连续擦除）/ 当前颜色块 / 清空（垃圾桶）
- 颜色行（简化）：一排 11 色色块，紧贴网格上方快速选色
- 填色与删除都支持点击 + 长按拖动连续操作；
- 数据按模块名持久化到 perler_data.json；
- AI 可调用 perler_draw 工具按描述在拼豆上绘制。

规则配置：source.ui = "perler"。
"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

import data_store
from widgets import ModuleWidget, kit
from widgets import icons

_PALETTE = kit.PALETTE   # 画布/拼豆共用一份（见 kit.PALETTE）

_COLS = 11            # 列数
_ROWS = 10            # 行数
_CELL = 24            # 每格 16px → 网格 11*16 ≈ 176px（填满内容区，右侧无空隙）
_GRID_H = _CELL * _ROWS + 4


def _pix_icon(kind):
    """共享高清图标：pen 填色笔 / erase 橡皮 / trash 清空。"""
    return icons.icon(kind)


class Widget(ModuleWidget):
    """拼豆：11x10 像素填色。"""

    FIX_H = 22.5
    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._color = _PALETTE[0]
        self._tool = "pen"            # pen / erase
        self._cells = {}              # (r, c) -> color
        self.setFixedHeight(self.current_height())

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._ai_btn = kit.ghost_btn("AI", 33, 13, "AI 绘画：让 AI 按描述在拼豆上画像素图")
        self._ai_btn.clicked.connect(self._ai_draw)
        self._fold_btn = kit.expand_btn("展开")
        self._fold_btn.clicked.connect(self._toggle_fold)
        self._row, rl, self._title, self._summary = kit.module_row(
            "拼豆", "11×10", actions=(self._ai_btn, self._fold_btn))
        self._root.addWidget(self._row)

        # ---- 展开区 ----
        self._expand = QWidget(self)
        el = QVBoxLayout(self._expand)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(kit.bs(3))

        # 工具行：填色 / 删除 / 当前颜色块 / 清空 —— 自适应拉长填满
        bar1 = QWidget(self._expand)
        bar1.setFixedHeight(kit.bs(24))
        b1 = kit.row(margins=(0, 0, 0, 0), spacing=3)
        self._btn_pen = self._tool_btn(bar1, "pen", "填色")
        self._btn_erase = self._tool_btn(bar1, "erase", "删除")
        b1.addWidget(self._btn_pen, 1, Qt.AlignVCenter)     # stretch 拉长
        b1.addWidget(self._btn_erase, 1, Qt.AlignVCenter)
        # 当前颜色块（长方形，点击也作为填色提示）
        self._cur_color = QLabel("", bar1)
        self._cur_color.setFixedHeight(kit.bs(21))
        self._cur_color.setCursor(Qt.PointingHandCursor)
        self._cur_color.setToolTip("当前颜色")
        b1.addWidget(self._cur_color, 1, Qt.AlignVCenter)
        # 清空：非勾选式垃圾桶按钮
        self._clear_btn = QPushButton("", bar1)
        self._clear_btn.setToolTip("清空")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setFixedHeight(kit.bs(21))
        self._clear_btn.setIcon(_pix_icon("trash"))
        self._clear_btn.setIconSize(__import__("PyQt5.QtCore",
                                               fromlist=["QSize"]).QSize(kit.bs(19.5), kit.bs(19.5)))
        self._clear_btn.clicked.connect(self._clear_all)
        self._clear_btn.setStyleSheet(kit.scale_qss(
            "QPushButton{border:1.5px solid rgba(255,255,255,40);border-radius:4.5px;"
            "background:transparent;}"
            "QPushButton:hover{background:rgba(74,144,226,90);}"))
        b1.addWidget(self._clear_btn, 1, Qt.AlignVCenter)
        bar1.setLayout(b1)
        el.addWidget(bar1)

        # 颜色行（简化）：一排色块，紧贴网格上方
        bar2 = QWidget(self._expand)
        bar2.setFixedHeight(kit.bs(24))
        b2 = kit.row(margins=(0, 0, 0, 0), spacing=3)
        self._color_btns = []
        for c in _PALETTE:
            cb = QPushButton("", bar2)
            cb.setToolTip(c)
            cb.setFixedSize(kit.bs(21), kit.bs(21))
            cb.setCursor(Qt.PointingHandCursor)
            cb.setStyleSheet(kit.scale_qss(
                "QPushButton{border:1.5px solid rgba(255,255,255,70);"
                "border-radius:10.5px;background:%s;}" % c))
            cb.clicked.connect(lambda _=False, cc=c: self._set_color(cc))
            self._color_btns.append(cb)
            b2.addWidget(cb, 1, Qt.AlignVCenter)    # 色块拉长铺满一行（间距均匀）
        bar2.setLayout(b2)
        el.addWidget(bar2)

        # 像素网格：固定高度容器，顶部贴紧颜色行，水平居中（不留弹性空隙）
        grid_host = QWidget(self._expand)
        gh = __import__("PyQt5.QtWidgets",
                       fromlist=["QHBoxLayout"]).QHBoxLayout(grid_host)
        gh.setContentsMargins(0, 0, 0, 0)
        self._grid = _PerlerGrid(grid_host, _COLS, _ROWS, kit.bs(_CELL))
        self._grid.on_cell = self._apply_cell
        gh.addWidget(self._grid, 0, Qt.AlignHCenter)   # 水平居中，不留左右空隙
        grid_host.setLayout(gh)
        el.addWidget(grid_host, 0)   # 不伸缩 → 顶对齐，无下方空隙

        # 状态行
        self._status = kit.caption("")
        el.addWidget(self._status)

        self._root.addWidget(self._expand)
        self._expand.hide()

        self._load()
        self._grid.set_cells(self._cells)
        self._refresh_ui()
        self._set_tool("pen")
        self._set_color(self._color)

    def _tool_btn(self, parent, kind, tip):
        b = kit.icon_button(parent)
        b.setToolTip(tip)
        b.setCheckable(True)
        b.setIcon(_pix_icon(kind))
        from PyQt5.QtCore import QSize
        b.setIconSize(QSize(kit.bubble_token("icon"), kit.bubble_token("icon")))
        b.clicked.connect(lambda _=False, k=kind: self._set_tool(k))
        b.setStyleSheet(kit.scale_qss(
            "QPushButton{border:1.5px solid rgba(255,255,255,40);border-radius:4.5px;"
            "background:transparent;}"
            "QPushButton:checked{border-color:#4a90e2;"
            "background:rgba(74,144,226,120);}"
            "QPushButton:hover{background:rgba(74,144,226,90);}"))
        return b

    # ---------- 工具 ----------
    def _set_tool(self, t):
        self._tool = t
        self._btn_pen.setChecked(t == "pen")
        self._btn_erase.setChecked(t == "erase")

    def _apply_cell(self, r, c):
        """网格点击/拖动回调：填色 / 删除。"""
        if not (0 <= r < _ROWS and 0 <= c < _COLS):
            return
        key = (r, c)
        if self._tool == "erase":
            if key in self._cells:
                del self._cells[key]
                self._grid.set_cell(r, c, None)
                self._schedule_save()
        else:  # pen
            self._cells[key] = self._color
            self._grid.set_cell(r, c, self._color)
            self._schedule_save()
        self._refresh_ui()

    def _set_color(self, c):
        self._color = c
        self._grid.set_pen_color(c)
        self._set_tool("pen")   # 选色后自动切回填色模式
        self._cur_color.setStyleSheet(kit.scale_qss(
            "QLabel{border:1.5px solid rgba(255,255,255,70);border-radius:4.5px;"
            "background:%s;}" % c))
        for cb, cc in zip(self._color_btns, _PALETTE):
            cb.setStyleSheet(kit.scale_qss(
                "QPushButton{border:3px solid %s;border-radius:10.5px;background:%s;}"
                % ("#ffffff" if cc == c else "rgba(255,255,255,70)", cc)))

    def _clear_all(self):
        if not self._cells:
            return
        if not kit.confirm(self, "清空拼豆", "确定清空所有像素？", danger=True):
            return
        self._cells.clear()
        self._grid.clear_all()
        self._refresh_ui()
        self._schedule_save()

    # ---------- 显示 ----------
    def _refresh_ui(self):
        self._summary.set_text("11×10  %d 格" % len(self._cells))
        self._status.set_text("已填 %d / %d 格" % (len(self._cells), _COLS * _ROWS))

    # ---------- 持久化 ----------
    def _key(self):
        return str(self.rule.get("name", "拼豆") or "拼豆")

    def _load(self):
        try:
            d = data_store.read_dict("perler_data.json")
            rec = d.get(self._key()) or {}
            cells = rec.get("cells") or {}
            self._cells = {}
            for k, v in cells.items():
                try:
                    r, c = k.split(",")
                    self._cells[(int(r), int(c))] = str(v)
                except Exception:
                    continue
        except Exception:
            self._cells = {}

    def _save(self):
        try:
            def _mut(d):
                cells = {("%d,%d" % k): v for k, v in self._cells.items()}
                d[self._key()] = {"cols": _COLS, "rows": _ROWS, "cells": cells}
                return d
            data_store.mutate_json("perler_data.json", {}, _mut)
        except Exception:
            pass

    def _schedule_save(self):
        try:
            if not hasattr(self, "_save_timer"):
                self._save_timer = QTimer(self)
                self._save_timer.setSingleShot(True)
                self._save_timer.timeout.connect(self._save)
            self._save_timer.start(500)
        except Exception:
            self._save()

    # ---------- AI 绘画（按钮入口 + perler_draw 工具入口） ----------
    def _ai_draw(self):
        """AI 绘画按钮：弹窗输入描述 → 调 ai_draw 在拼豆上绘制。"""
        from PyQt5.QtWidgets import QApplication, QInputDialog
        dlg = QInputDialog(self)
        dlg.setWindowTitle("AI 拼豆绘画")
        dlg.setLabelText("描述想画的像素图案（如：画一个红心）：")
        dlg.setOption(QInputDialog.UsePlainTextEditForTextInput, True)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        dlg.setStyleSheet(kit.DIALOG_BTN_QSS)   # 按钮规范统一
        dlg.adjustSize()   # 窗口自适应内容尺寸
        ok = dlg.exec_()
        text = dlg.textValue() if ok else ""
        if not ok or not text.strip():
            return
        if self._collapsed:
            self._toggle_fold()
        self._status.set_text("AI 绘制中…")
        QApplication.processEvents()
        try:
            res = self.ai_draw(text.strip())
            self._status.set_text(str(res))
        except Exception as e:
            self._status.set_text("AI 绘制失败：%s" % e)

    def ai_draw(self, desc):
        """按描述在拼豆上绘制（按钮入口；主线程调用）。"""
        from status_monitor import _llm_json_array
        prompt = (
            "你在一个 11 列 x 10 行的像素画板上作画，坐标 x 为列 0-10、y 为行 0-9。"
            "根据用户描述输出 JSON 数组，每个元素："
            '{"x":列0-10,"y":行0-9,"color":"#rrggbb"}。'
            "尽量让图案居中、像素化、有轮廓。只输出 JSON 数组，不要其他文字。"
            "用户描述：" + desc)
        try:
            cells = _llm_json_array(prompt)
        except Exception as e:
            return "AI 绘制失败：%s" % e
        if not isinstance(cells, list) or not cells:
            return "AI 未返回像素指令"
        n = self.apply_cells(cells)
        return "已绘制 %d 个像素" % n

    def apply_cells(self, cells):
        """把 AI 生成的像素坐标列表应用到拼豆网格（主线程），返回绘制像素数。"""
        n = 0
        for c in cells:
            try:
                x = int(c.get("x", 0))
                y = int(c.get("y", 0))
                if 0 <= x < _COLS and 0 <= y < _ROWS:
                    color = str(c.get("color", "#ffffff"))
                    self._cells[(y, x)] = color
                    self._grid.set_cell(y, x, color)
                    n += 1
            except Exception:
                continue
        self._refresh_ui()
        self._schedule_save()
        return n

    # ---------- 展开 ----------
    def current_height(self):
        if self._collapsed:
            return kit.row_height()
        # 标题/工具/状态高度统一取 kit 规范，间距也随档位缩放。
        grid_h = kit.bs(_CELL) * _ROWS + 2
        return (kit.header_row_height(True) + kit.bs(3) + kit.toolbar_height() + kit.bs(3)
                + kit.toolbar_height() + kit.bs(3) + grid_h + kit.bs(3)
                + kit.caption_height())

    def _toggle_fold(self):
        self._collapsed = not self._collapsed
        self._fold_btn.setText("收起" if not self._collapsed else "展开")
        self._expand.setVisible(not self._collapsed)
        self.setFixedHeight(self.current_height())
        if not self._collapsed:
            self._grid.set_cells(self._cells)
            self._refresh_ui()
        if getattr(self, "on_resize", None):
            try:
                self.on_resize()
            except Exception:
                pass

    # ---------- 框架回调 ----------
    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._title.setText(name)
        self._load()
        self._grid.set_cells(self._cells)
        self._refresh_ui()

    def render(self, state, value):
        pass


class _PerlerGrid(QWidget):

    def __init__(self, parent, cols, rows, cell):
        super().__init__(parent)
        self._cols = cols
        self._rows = rows
        self._cell = cell
        self.setFixedSize(cell * cols + 2, cell * rows + 2)
        self.setMouseTracking(True)
        self._cells = {}
        self._pen_color = "#ff6b6b"
        self._pressing = False
        self.on_cell = None

    def set_pen_color(self, c):
        self._pen_color = c

    def set_cells(self, cells):
        self._cells = dict(cells)
        self.update()

    def set_cell(self, r, c, color):
        self._cells[(r, c)] = color if color else None
        if not color:
            self._cells.pop((r, c), None)
        self.update()

    def clear_all(self):
        self._cells.clear()
        self.update()

    def _cell_at(self, pos):
        x = int((pos.x() - 1) // self._cell)
        y = int((pos.y() - 1) // self._cell)
        if 0 <= x < self._cols and 0 <= y < self._rows:
            return y, x
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressing = True
            key = self._cell_at(e.pos())
            if key and self.on_cell:
                self.on_cell(*key)
            e.accept()

    def mouseMoveEvent(self, e):
        if self._pressing and (e.buttons() & Qt.LeftButton):
            key = self._cell_at(e.pos())
            if key and self.on_cell:
                self.on_cell(*key)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._pressing = False
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cell = self._cell
        radius = max(2, cell // 5)
        p.setBrush(QColor(28, 33, 44, 220))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 4, 4)
        for r in range(self._rows):
            for c in range(self._cols):
                x = 1 + c * cell
                y = 1 + r * cell
                color = self._cells.get((r, c))
                if color:
                    p.setBrush(QColor(color))
                    p.setPen(Qt.NoPen)
                    p.drawRoundedRect(x, y, cell - 2, cell - 2, radius, radius)
                else:
                    p.setBrush(QColor(255, 255, 255, 14))
                    p.setPen(QPen(QColor(255, 255, 255, 35), 1))
                    p.drawRoundedRect(x, y, cell - 2, cell - 2, radius, radius)
        p.end()
