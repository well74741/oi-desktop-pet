# -*- coding: utf-8 -*-
"""离屏验证：bubble_scale=2.0 时所有模块组件能创建并展开（内容填充、按钮/字号一致）。"""
import os
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1.5"

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

app = QApplication(sys.argv)

from widgets import kit
kit.set_bubble_scale(2.0)
kit.set_pet_scale(2.0)

from widgets import load_module_widget

NAMES = ["perler", "canvas", "tokenmeter", "stats", "todo",
         "notes", "countdown", "calc", "tomato"]
fails = []
for name in NAMES:
    try:
        w, err = load_module_widget(name)
        if w is None:
            fails.append("%s: %s" % (name, err))
            continue
        w.setFixedWidth(kit.bs(210))
        # 模拟展开：调用各模块的展开入口（_toggle_fold / expand）
        fold = getattr(w, "_toggle_fold", None)
        if fold is not None:
            if w._collapsed:
                fold()
        else:
            expand = getattr(w, "expand", None) or getattr(w, "set_expanded", None)
            if expand is not None:
                try:
                    expand(True)
                except TypeError:
                    expand()
        h = getattr(w, "current_height", None)
        if callable(h):
            cur = h()
        else:
            cur = h
        min_h = max(30, kit.bs(int(getattr(w, "FIX_H", 0) or 0)))
        if cur is None or int(cur) < min_h:
            fails.append("%s: current_height=%s < %s" % (name, cur, min_h))
        if w.width() != kit.bs(210):
            fails.append("%s: width=%s != %s" % (name, w.width(), kit.bs(210)))
        # 触发一次布局
        w.adjustSize()
        w.show()
        app.processEvents()
        if name == "perler":
            # A custom painted child can "load" while its paintEvent silently
            # fails, so render it and verify both grid pixels and tool glyphs.
            w.apply_cells([{"x": 0, "y": 0, "color": "#ff6b6b"},
                           {"x": 10, "y": 9, "color": "#4dabf7"}])
            app.processEvents()
            img = w._grid.grab().toImage()
            cell = img.width() // 12
            got_first = img.pixelColor(cell // 2, cell // 2).name().lower()
            got_last = img.pixelColor(img.width() - cell // 2,
                                      img.height() - cell // 2).name().lower()
            if got_first != "#ff6b6b" or got_last != "#4dabf7":
                fails.append("perler: blank grid (%s, %s)" % (got_first, got_last))
            from widgets import icons
            for kind in ("pen", "erase", "trash"):
                icon_img = icons.pixmap(kind).toImage()
                painted = any(
                    icon_img.pixelColor(x, y).alpha() > 0
                    for y in range(0, icon_img.height(), 4)
                    for x in range(0, icon_img.width(), 4)
                )
                if icon_img.isNull() or not painted:
                    fails.append("perler icon %s: null pixmap" % kind)
        print("OK  %-11s expand_h=%s size=%s" % (name, cur, (w.width(), w.height())))
    except Exception:
        fails.append(name)
        traceback.print_exc()

print("----")
if fails:
    print("FAIL:", ", ".join(fails))
else:
    print("ALL COMPONENTS LOAD+EXPAND OK at bubble_scale=2.0")
    sys.exit(0)
sys.exit(1)
