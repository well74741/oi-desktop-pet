# -*- coding: utf-8 -*-
"""倒计时器组件：输入分钟/秒数，开始/暂停/重置，到点弹气泡提示。

规则配置里 source.ui 填 "countdown" 即可。
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QSpinBox

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """倒计时组件：分钟输入 + 开始/暂停/重置，到点提醒。"""

    FIX_H = 90

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0
        self._left = 0.0
        self._running = False
        self._deadline = 0.0

        self._min_spin = QSpinBox(self)
        self._min_spin.setRange(0, 600)
        self._min_spin.setSuffix(" 分")
        self._min_spin.setFixedWidth(kit.bs(70))
        self._sec_spin = QSpinBox(self)
        self._sec_spin.setRange(0, 59)
        self._sec_spin.setSuffix(" 秒")
        self._sec_spin.setFixedWidth(kit.bs(70))
        for s in (self._min_spin, self._sec_spin):
            s.setStyleSheet(kit.scale_qss(
                "QSpinBox{background:rgba(255,255,255,22);border:1px solid "
                "rgba(255,255,255,45);border-radius:4px;color:#e8ecf5;"
                "font-family:SimHei;font-size:10px;}"))

        self._label = kit.lab("0:00", size=20, bold=True,
                              align=Qt.AlignCenter, wrap=False)
        self._toggle_btn = kit.btn("开始", primary=True)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle)
        self._reset_btn = kit.btn("重置", small=True)
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.clicked.connect(self._reset)
        self._preset = kit.btn("5分", small=True)
        self._preset.setCursor(Qt.PointingHandCursor)
        self._preset.clicked.connect(lambda: self._set_preset(5 * 60))
        self._preset2 = kit.btn("25分", small=True)
        self._preset2.setCursor(Qt.PointingHandCursor)
        self._preset2.clicked.connect(lambda: self._set_preset(25 * 60))

        lay = kit.col(
            kit.row(self._min_spin, self._sec_spin),
            self._label,
            kit.row(self._toggle_btn, self._reset_btn, kit.hsep(),
                    self._preset, self._preset2),
            spacing=3, margins=(4, 2, 4, 3))
        self.setLayout(lay)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._load()

    def _key(self):
        return str(self.rule.get("name", "倒计时") or "倒计时")

    def _load(self):
        try:
            d = data_store.read_dict("countdown_data.json")
            self._total = int(d.get(self._key(), 0) or 0)
        except Exception:
            self._total = 0
        self._left = float(self._total)
        self._update_label()

    def _save(self):
        try:
            def _mut(d):
                d[self._key()] = int(self._total)
                return d
            data_store.mutate_json("countdown_data.json", {}, _mut)
        except Exception:
            pass

    def _update_label(self):
        s = max(0, int(round(self._left)))
        self._label.setText("%d:%02d" % (s // 60, s % 60))

    def _set_preset(self, secs):
        self._total = secs
        self._left = float(secs)
        self._running = False
        self._timer.stop()
        self._toggle_btn.setText("开始")
        self._update_label()
        self._save()

    def _toggle(self):
        if self._running:
            self._running = False
            self._timer.stop()
            self._toggle_btn.setText("继续")
            return
        if self._left <= 0:
            self._left = float(
                self._min_spin.value() * 60 + self._sec_spin.value())
            self._total = int(self._left)
        self._running = True
        self._deadline = time.monotonic() + self._left
        self._timer.start()
        self._toggle_btn.setText("暂停")

    def _reset(self):
        self._running = False
        self._timer.stop()
        self._left = float(self._total)
        self._toggle_btn.setText("开始")
        self._update_label()

    def _tick(self):
        self._left = self._deadline - time.monotonic()
        if self._left <= 0:
            self._left = 0
            self._running = False
            self._timer.stop()
            self._toggle_btn.setText("开始")
            self._update_label()
            # 到点：气泡提示
            try:
                from status_monitor import _tool_bridge
                _tool_bridge.popup.emit(
                    "⏰ %s 倒计时结束！" % self._key())
            except Exception:
                pass
            return
        self._update_label()

    def render(self, state, value):
        pass
