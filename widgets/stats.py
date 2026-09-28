# -*- coding: utf-8 -*-
"""通用统计表组件：单行显示当前/今日统计，点击展开（24 小时柱状图 +
说明）。数据源可配：内置指标（待办/模块/按钮/聊天/工具/token）或 HTTP 接口。

规则配置里 source.ui 填 "stats"，统计配置放 source.stats：
{
  "source": {"type": "script", "lang": "python", "ui": "stats",
             "code": "result = ''", "stats": {"metric": "todos"}},
  "interval": 60
}
stats 字段：
- metric: todos / modules / buttons / chat / tool / token（内置指标）
- title: 显示标题（默认用模块名）
- http_url: HTTP 数据源 URL（可选，优先级高于 metric）
- http_path: JSON 取值路径，如 data.value 或 data.items[0].n
- unit: 数值单位（默认 无）
"""
import json
import time
import urllib.request

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QLabel, QPushButton, QVBoxLayout, QWidget)

from status_monitor import (_load_token_usage, _today_token_summary,
                            _load_usage, _usage_today_hourly,
                            _usage_daily_summary)
from widgets import ModuleWidget, kit


# ---------- 内置指标取值 ----------

def _count_todos():
    import data_store
    return len(data_store.read_list("todo_data.json") or [])


def _count_modules():
    from pet_gravity import load_settings
    return len([r for r in (load_settings().get("status_rules") or [])
                if r.get("enabled", True)])


def _count_buttons():
    from pet_gravity import load_settings
    return len(load_settings().get("slot_shortcuts") or [])


def _metric_value(metric):
    """返回 (当前值, 今日小时数组)。"""
    if metric == "todos":
        return _count_todos(), [0] * 24
    if metric == "modules":
        return _count_modules(), [0] * 24
    if metric == "buttons":
        return _count_buttons(), [0] * 24
    if metric == "chat":
        hourly = _usage_today_hourly(_load_usage(), kinds=("chat",))
        return sum(hourly), hourly
    if metric == "tool":
        hourly = _usage_today_hourly(_load_usage(), kinds=("tool",))
        return sum(hourly), hourly
    if metric == "token":
        hourly = _today_token_summary(_load_token_usage())
        return sum(hourly), hourly
    # 默认待办
    return _count_todos(), [0] * 24


def _fetch_http_stat(url, path):
    """HTTP 数据源：GET 返回 JSON，按 path 取值。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "Chrome/126.0.0.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cur = data
    for part in str(path or "value").split("."):
        part = part.strip()
        if not part:
            continue
        if part.endswith("]") and "[" in part:
            name, idx = part[:-1].split("[")
            cur = cur.get(name) if isinstance(cur, dict) else None
            try:
                cur = cur[int(idx)]
            except Exception:
                return None, [0] * 24
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None, [0] * 24
    try:
        return float(cur), [0] * 24
    except Exception:
        return None, [0] * 24


# ---------- 柱状图 ----------

class _StatBar(QWidget):
    """24 小时柱状图：网格线 + 悬停即时显示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hourly = [0] * 24
        self._hover = -1
        self._unit = ""
        self.setMouseTracking(True)
        self.setFixedHeight(kit.bs(44))

    def set_data(self, hourly, unit=""):
        self._hourly = list(hourly or [0] * 24)
        self._unit = unit
        self.update()

    def _hover_tip(self):
        h = self._hover
        if 0 <= h < 24:
            return "%02d:00  %s%s" % (h, _fmt(self._hourly[h]), self._unit)
        return ""

    def mouseMoveEvent(self, e):
        h = int(e.x() / max(1, self.width()) * 24)
        if 0 <= h < 24 and h != self._hover:
            self._hover = h
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
        p.setBrush(QColor(30, 36, 48, 200))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 4, 4)
        # 网格线
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        plot_top, plot_bot = 4, h - 8
        for g in (0.25, 0.5, 0.75, 1.0):
            gy = plot_bot - (plot_bot - plot_top) * g
            p.drawLine(2, int(gy), w - 3, int(gy))
        mx = max(self._hourly) if self._hourly else 0
        if mx <= 0:
            p.setPen(QColor(150, 170, 205))
            p.setFont(kit.font_pt(8))
            p.drawText(self.rect(), Qt.AlignCenter, "今日暂无数据")
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


# ---------- 组件 ----------

class Widget(ModuleWidget):
    """通用统计表：单行（当前值 + 刷新 + 展开），展开显示 24 小时柱状图。"""

    FIX_H = 15
    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._cfg = {}
        self._value = 0
        self._hourly = [0] * 24
        self.setFixedHeight(self.current_height())

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._refresh_btn = kit.ghost_btn("⟳", tip="手动刷新")
        self._refresh_btn.clicked.connect(self._refresh_data)
        self._fold_btn = kit.expand_btn("详情")
        self._fold_btn.clicked.connect(self._toggle_fold)
        self._row, rl, self._title, self._summary = kit.module_row(
            "统计表", "—", actions=(self._refresh_btn, self._fold_btn))
        self._root.addWidget(self._row)

        self._expand = QWidget(self)
        el = QVBoxLayout(self._expand)
        el.setContentsMargins(0, 0, 0, kit.bs(0))
        el.setSpacing(kit.bs(2))
        self._chart = _StatBar(self._expand)
        el.addWidget(self._chart)
        self._desc = kit.caption("", wrap=True)
        el.addWidget(self._desc, 1)
        self._root.addWidget(self._expand)
        self._expand.hide()

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
        self._fold_btn.setText("收起" if not self._collapsed else "详情")
        self._expand.setVisible(not self._collapsed)
        self.setFixedHeight(self.current_height())
        if not self._collapsed:
            self._refresh_data()
        if getattr(self, "on_resize", None):
            try:
                self.on_resize()
            except Exception:
                pass

    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._title.setText(name)
        src = (rule or {}).get("source") or {}
        self._cfg = src.get("stats") or {}
        self._refresh_data()

    def _refresh_data(self):
        try:
            cfg = self._cfg or {}
            unit = str(cfg.get("unit", "") or "")
            http_url = str(cfg.get("http_url", "") or "")
            if http_url:
                val, hourly = _fetch_http_stat(
                    http_url, str(cfg.get("http_path", "value") or "value"))
                if val is None:
                    self._summary.set_text("获取失败")
                    return
            else:
                metric = str(cfg.get("metric", "todos") or "todos")
                val, hourly = _metric_value(metric)
            self._value = val
            self._hourly = hourly
            title = str(cfg.get("title", "") or "")
            if title:
                self._title.setText(title)
            # 单行摘要：今日/当前值
            if metric == "todos" or (http_url and "todos" not in str(cfg)):
                self._summary.set_text("%s%s" % (_fmt(val), unit))
            else:
                self._summary.set_text("今日 %s%s" % (_fmt(val), unit))
            self._chart.set_data(hourly, unit)
            desc = self._describe(cfg, val, unit)
            self._desc.setText(desc)
        except Exception:
            pass

    def _describe(self, cfg, val, unit):
        """展开区说明：指标名 + 今日值 + 按模块 TOP（若适用）。"""
        metric = str(cfg.get("metric", "") or "")
        lines = []
        if metric == "todos":
            lines.append("待办总数：%s%s" % (_fmt(val), unit))
        elif metric == "modules":
            lines.append("已启用模块数：%s%s" % (_fmt(val), unit))
        elif metric == "buttons":
            lines.append("径向菜单按钮数：%s%s" % (_fmt(val), unit))
        elif metric == "chat":
            lines.append("今日对话次数：%s%s" % (_fmt(val), unit))
            self._append_module_lines(lines, kinds=("chat",))
        elif metric == "tool":
            lines.append("今日工具调用：%s%s" % (_fmt(val), unit))
            self._append_module_lines(lines, kinds=("tool",))
        elif metric == "token":
            lines.append("今日消耗：%s%s" % (_fmt(val), unit))
        elif cfg.get("http_url"):
            lines.append("接口值：%s%s" % (_fmt(val), unit))
        else:
            lines.append("当前值：%s%s" % (_fmt(val), unit))
        return "\n".join(lines)

    def _append_module_lines(self, lines, kinds):
        try:
            from status_monitor import _usage_daily_summary as _ud
            events = _load_usage()
            agg = {}
            for e in events:
                if str(e.get("kind", "")) not in kinds:
                    continue
                m = str(e.get("module", "?"))
                agg[m] = agg.get(m, 0) + int(e.get("n", 1) or 1)
            top = sorted(agg.items(), key=lambda x: -x[1])[:4]
            for m, n in top:
                lines.append("· %s  %s" % (m, _fmt(n)))
        except Exception:
            pass

    def render(self, state, value):
        pass


def _fmt(n):
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)
