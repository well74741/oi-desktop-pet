# -*- coding: utf-8 -*-
"""健康打卡组件：喝水/运动/睡眠三项打卡，按天记录，可清零。

规则配置里 source.ui 填 "health" 即可。
数据存 health_data.json：{ 模块名: {date, water, exercise, sleep} }
"""
from datetime import date

from PyQt5.QtCore import Qt

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """健康打卡：喝水杯数 / 运动次数 / 睡眠时长（小时），每日记录。"""

    FIX_H = 195

    def __init__(self, parent=None):
        super().__init__(parent)
        self._water = 0
        self._exercise = 0
        self._sleep = 0.0
        self._day = ""

        self._w_label = kit.lab("喝水 0 杯", size=16.5, color="#7db6ff", bold=True)
        self._e_label = kit.lab("运动 0 次", size=16.5, color="#69db7c", bold=True)
        self._s_label = kit.lab("睡眠 0.0 h", size=16.5, color="#9775fa", bold=True)
        w_btn = kit.btn("+1", primary=True)
        w_btn.setCursor(Qt.PointingHandCursor)
        w_btn.clicked.connect(self._add_water)
        e_btn = kit.btn("+1", primary=True)
        e_btn.setCursor(Qt.PointingHandCursor)
        e_btn.clicked.connect(self._add_exercise)
        s_btn = kit.btn("+0.5", small=True)
        s_btn.setCursor(Qt.PointingHandCursor)
        s_btn.clicked.connect(self._add_sleep)
        reset = kit.btn("清零", small=True)
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset_day)

        lay = kit.col(
            kit.row(self._w_label, kit.hsep(), w_btn),
            kit.row(self._e_label, kit.hsep(), e_btn),
            kit.row(self._s_label, kit.hsep(), s_btn),
            kit.row(kit.lab("今日打卡", size=10.5, color="#96a7c4"), kit.hsep(), reset),
            spacing=4.5, margins=(6, 3, 6, 4.5))
        self.setLayout(lay)
        self._load()

    def _key(self):
        return str(self.rule.get("name", "健康打卡") or "健康打卡")

    def _load(self):
        try:
            d = data_store.read_dict("health_data.json")
            rec = d.get(self._key()) or {}
            if rec.get("date") == str(date.today()):
                self._water = int(rec.get("water", 0) or 0)
                self._exercise = int(rec.get("exercise", 0) or 0)
                self._sleep = float(rec.get("sleep", 0.0) or 0.0)
            else:
                self._water = self._exercise = 0
                self._sleep = 0.0
        except Exception:
            pass
        self._day = str(date.today())
        self._update()

    def _save(self):
        try:
            def _mut(d):
                d[self._key()] = {"date": self._day,
                                  "water": self._water,
                                  "exercise": self._exercise,
                                  "sleep": self._sleep}
                return d
            data_store.mutate_json("health_data.json", {}, _mut)
        except Exception:
            pass

    def _update(self):
        self._w_label.setText("喝水 %d 杯" % self._water)
        self._e_label.setText("运动 %d 次" % self._exercise)
        self._s_label.setText("睡眠 %.1f h" % self._sleep)

    def _add_water(self):
        self._water += 1
        self._update()
        self._save()

    def _add_exercise(self):
        self._exercise += 1
        self._update()
        self._save()

    def _add_sleep(self):
        self._sleep = round(self._sleep + 0.5, 1)
        self._update()
        self._save()

    def _reset_day(self):
        self._water = self._exercise = 0
        self._sleep = 0.0
        self._update()
        self._save()

    def render(self, state, value):
        pass
