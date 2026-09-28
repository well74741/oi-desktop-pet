# -*- coding: utf-8 -*-
"""Token 消耗检测组件：单行显示今日消耗，点击展开详情（24 小时柱状图 +
按模块列表，鼠标悬停柱状即时显示该小时用量）。

规则配置里 source.ui 填 "tokenmeter" 即可。
数据由 status_monitor 在每次大模型调用时记录到 token_usage.json。
"""
import time

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QLabel, QPushButton, QVBoxLayout, QWidget)

from status_monitor import (_load_token_usage, _today_token_summary,
                            _module_token_summary)
from widgets import ModuleWidget, kit


class _HourBar(QWidget):
    """24 小时柱状图（紧凑）：背景网格线 + 悬停即时显示该小时用量。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hourly = [0] * 24
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(kit.bs(44))

    def set_data(self, hourly):
        self._hourly = list(hourly)
        self.update()

    def _hover_tip(self):
        h = self._hover
        if 0 <= h < 24:
            return "%02d:00  %s" % (h, _fmt(self._hourly[h]))
        return ""

    def mouseMoveEvent(self, e):
        h = int(e.x() / max(1, self.width()) * 24)
        if 0 <= h < 24 and h != self._hover:
            self._hover = h
            # 即时显示，不等系统 tooltip 延迟
            try:
                from PyQt5.QtWidgets import QToolTip as _QT
                # 第三个参数必须传控件：不传的话 QToolTip 用系统调色板
                # ToolTipBase(#ffffdc)，就是那块黄底；传了才吃得到本窗口的
                # 深色 QToolTip 样式（和「模块列表」标题那次是同一个坑）
                _QT.showText(self.mapToGlobal(QPoint(e.x() + 8, 2)),
                             self._hover_tip(), self)
            except Exception:
                pass
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = -1
        try:
            from PyQt5.QtWidgets import QToolTip as _QT
            _QT.hideText()
        except Exception:
            pass
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 背景
        p.setBrush(QColor(30, 36, 48, 200))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 4, 4)
        # 水平网格线（25%/50%/75%/100% 基准线）
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        plot_top = 4
        plot_bot = h - 8
        for g in (0.25, 0.5, 0.75, 1.0):
            gy = plot_bot - (plot_bot - plot_top) * g
            p.drawLine(2, int(gy), w - 3, int(gy))
        mx = max(self._hourly) if self._hourly else 0
        if mx <= 0:
            p.setPen(QColor(150, 170, 205))
            p.setFont(kit.font_pt(8))
            p.drawText(self.rect(), Qt.AlignCenter, "今日暂无消耗")
            return
        bw = max(2, int(w / 24) - 2)
        for i, v in enumerate(self._hourly):
            x = int(i * w / 24) + 1
            bh = int(max(0, v) / mx * (plot_bot - plot_top))
            y = plot_bot - bh
            if v > 0:
                if i == time.localtime().tm_hour:
                    p.setBrush(QColor(120, 200, 255, 230))
                else:
                    p.setBrush(QColor(74, 144, 226, 170))
                p.setPen(Qt.NoPen)
                p.drawRect(x, y, bw, bh)
            if i % 6 == 0:
                p.setPen(QColor(120, 135, 165))
                p.setFont(kit.font_pt(6))
                p.drawText(x, h - 4, str(i))
        p.end()


class Widget(ModuleWidget):
    """Token 消耗检测：单行（今日消耗 + 刷新 + 展开按钮），展开显示柱状图与模块列表。"""

    FIX_H = 15
    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._records = []
        self.setFixedHeight(self.current_height())

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._refresh_btn = kit.ghost_btn("⟳", tip="手动刷新统计")
        self._refresh_btn.clicked.connect(self._refresh_data)
        self._fold_btn = kit.expand_btn("展开")
        self._fold_btn.clicked.connect(self._toggle_fold)
        self._row, rl, self._title, self._summary = kit.module_row(
            "Token 消耗", "—", actions=(self._refresh_btn, self._fold_btn))
        self._root.addWidget(self._row)

        # 详情区（顶部紧贴标题行）：柱状图 + 模块列表
        self._expand = QWidget(self)
        el = QVBoxLayout(self._expand)
        el.setContentsMargins(0, 0, 0, kit.bs(0))
        el.setSpacing(kit.bs(2))
        self._chart = _HourBar(self._expand)
        el.addWidget(self._chart)
        self._mod_list = kit.caption("", wrap=True)
        el.addWidget(self._mod_list, 1)
        self._root.addWidget(self._expand)
        self._expand.hide()

        # 每分钟自动刷新
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self._refresh_data)
        self._timer.start()
        self._refresh_data()

    def current_height(self):
        if self._collapsed:
            return kit.row_height()
        return (kit.header_row_height(True) + kit.bs(2) + kit.bs(74)
                + kit.bs(2) + kit.caption_height())

    def _toggle_fold(self):
        self._collapsed = not self._collapsed
        self._fold_btn.setText("收起" if not self._collapsed else "展开")
        self._expand.setVisible(not self._collapsed)
        self.setFixedHeight(self.current_height())
        if not self._collapsed:
            self._refresh_data()
        if getattr(self, "on_resize", None):
            try:
                self.on_resize()
            except Exception:
                pass

    def _refresh_data(self):
        try:
            self._records = _load_token_usage()
            hourly = _today_token_summary(self._records)
            total = sum(hourly)
            self._summary.set_text("今日 %s" % _fmt(total))
            self._chart.set_data(hourly)
            today = time.strftime("%Y-%m-%d")
            today_mods = [(m, t) for m, t in _module_token_summary(self._records)
                          if any(str(r.get("t", "")).startswith(today)
                                 and r.get("module") == m for r in self._records)]
            lines = ["按模块："]
            if today_mods:
                for m, t in today_mods[:6]:
                    lines.append("· %s  %s" % (m, _fmt(t)))
            else:
                lines.append("（今日暂无）")
            self._mod_list.setText("\n".join(lines))
        except Exception:
            pass

    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._title.setText(name)

    def render(self, state, value):
        pass


def _fmt(n):
    """1000 -> 1.0k, 1000000 -> 1.0M"""
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)
