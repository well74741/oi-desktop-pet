# -*- coding: utf-8 -*-
"""组件模板：复制这个文件改成你的组件，在 widgets/ 目录即可。

使用步骤：
1. 复制本文件为 widgets/my_widget.py（文件名 = 组件名）；
2. 把类名改成 Widget（保留）；改 __init__ 里的界面；
3. 添加模块时 source.ui 填文件名（如 "my_widget"）；
4. 重启桌宠生效。

约定：
- 类名必须叫 Widget，继承 ModuleWidget；
- 主线程创建，可自由用 Qt 控件；
- 定义 FIX_H 固定高度（或靠 sizeHint 自适应）；
- 想做成"可展开"结构参考 canvas.py / tokenmeter.py（标题行常驻 15px +
  下方内容区，current_height / _toggle_fold）；
- 数据持久化用 data_store（见 _save/_load 示例）；
- 组件异常只影响该模块，不会拖垮桌宠。
"""
from PyQt5.QtCore import Qt, QTimer

import data_store
from widgets import ModuleWidget, kit


class Widget(ModuleWidget):
    """示例组件：标题 + 计数 + 开关 + 进度条 + 可展开说明区。"""

    FIX_H = 90            # 固定高度；内容会变化的组件不要 setFixedHeight

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
        self._bar = 40

        # ---------- 界面（kit 工具库，风格统一） ----------
        self._title = kit.lab("模板", size=11, bold=True)
        self._counter = kit.lab("计数 0", size=10, color="#7db6ff")
        btn_plus = kit.btn("＋1", primary=True)
        btn_plus.setCursor(Qt.PointingHandCursor)
        btn_plus.clicked.connect(self._inc)
        btn_reset = kit.btn("清零", small=True)
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset)

        self._switch = kit.switch(False, on_text="开", off_text="关")
        self._progress = kit.progress(self._bar, 100, height=8)
        # 进度条动起来（示例定时器）
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick_bar)
        self._timer.start()

        lay = kit.col(
            kit.row(self._title, kit.hsep(), self._counter),
            kit.row(btn_plus, btn_reset, self._switch),
            self._progress,
            margins=(6, 4, 6, 4))
        self.setLayout(lay)
        self._load()

    # ---------- 按钮逻辑 ----------
    def _inc(self):
        self._count += 1
        self._counter.setText("计数 %d" % self._count)
        self._save()

    def _reset(self):
        self._count = 0
        self._counter.setText("计数 0")
        self._save()

    def _tick_bar(self):
        self._bar = (self._bar + 10) % 110
        self._progress.setValue(min(100, self._bar))

    # ---------- 数据持久化（按模块名存 JSON） ----------
    def _key(self):
        return str(self.rule.get("name", "模板") or "模板")

    def _load(self):
        try:
            d = data_store.read_dict("mytemplate_data.json")
            self._count = int(d.get(self._key(), 0) or 0)
        except Exception:
            self._count = 0
        self._counter.setText("计数 %d" % self._count)

    def _save(self):
        try:
            def _mut(d):
                d[self._key()] = self._count
                return d
            data_store.mutate_json("mytemplate_data.json", {}, _mut)
        except Exception:
            pass

    # ---------- 框架回调 ----------
    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._title.setText(name)
        self._load()

    def render(self, state, value):
        """每帧刷新调用（value 是脚本取到的值），不需要可留空。"""
        pass
