# -*- coding: utf-8 -*-
"""基准/回归两用：把关键窗口的实测几何 + 字号导成 JSON。

改造前跑一次存 baseline.json，改造后再跑一次对比 —— 完全一致才算"显示效果不变"。
用法：python _check_ui_scale.py [--save 输出.json | --cmp 基准.json]
"""
import json
import os
import sys
import tempfile

os.environ.pop("QT_QPA_PLATFORM", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import data_store                                     # noqa: E402

SANDBOX = tempfile.mkdtemp(prefix="oi_uiscale_")
data_store.DATA_DIR = SANDBOX

from PyQt5.QtCore import Qt                           # noqa: E402
from PyQt5.QtWidgets import QApplication, QPushButton, QLabel, QLineEdit   # noqa: E402

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication([])

from widgets import kit                               # noqa: E402

kit.install_ui_zoom(app)

import module_editor as ME                            # noqa: E402
import module_templates as mt                         # noqa: E402
import pet_gravity as G                               # noqa: E402
import update_ui                                      # noqa: E402
import updater                                        # noqa: E402

out = {}


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def rect(name, w):
    """一个控件自己的几何 + 字号 + 关键内边距。"""
    f = w.font()
    out[name] = {
        "size": [w.width(), w.height()],
        "min": [w.minimumWidth(), w.minimumHeight()],
        "max": [w.maximumWidth(), w.maximumHeight()],
        "font_px": f.pixelSize(),
        "font_pt": round(f.pointSizeF(), 2),
        "margins": [w.layout().contentsMargins().left(), w.layout().contentsMargins().top(),
                    w.layout().contentsMargins().right(), w.layout().contentsMargins().bottom()]
                   if w.layout() else None,
        "spacing": w.layout().spacing() if w.layout() else None,
    }


# ---- 1. 设置窗 ----
sd = G.SettingsDialog(G.load_settings())
sd.show()
pump()
rect("settings", sd)
for b in sd.findChildren(QPushButton):
    if b.text() in ("确定", "取消", "+ 添加", "编辑"):
        rect("settings_btn_%s" % b.text(), b)
        break
out["settings_tabs"] = [sd._tab_builtin.text(), sd._tab_custom.text()]
out["rule_row_sizehint"] = [sd.status_rules_list.sizeHintForRow(0)] if sd.status_rules_list.count() else []

# ---- 2. 模块编辑窗（添加 / 编辑）----
e = ME.ModuleEditor(None, None, {"ai_profile": {}})
e.show()
pump()
rect("editor_add", e)
rect("editor_list", e.tpl_list)
rect("editor_ok", e.ok_btn)
e.close()
e = ME.ModuleEditor(None, mt.BY_KEY["countdown"].build("下班", {}), {"ai_profile": {}})
e.show()
pump()
rect("editor_edit", e)
e.close()

# ---- 3. 检查更新窗 ----
_mode = updater.install_mode
updater.install_mode = lambda: "installed"
try:
    u = update_ui.UpdateDialog(None, None, {
        "version": "9.9.9", "tag": "v9.9.9", "title": "v9.9.9",
        "notes": "## 改动\n- 一\n- 二", "asset": {"name": "s.exe", "size": 1}})
    u.show()
    pump()
    rect("update", u)
    btns = [b for b in u.findChildren(QPushButton) if b.isVisible() and b.text()]
    if btns:
        rect("update_btn", btns[0])
    u.close()
finally:
    updater.install_mode = _mode

# ---- 4. 桌宠 + 气泡 ----
pet = G.GravityPet(G.load_settings())
pet.move(-3000, 300)
pet.show()
pump()
out["pet"] = {"size": [pet.width(), pet.height()],
              "pet_size_setting": pet.settings.get("pet_size"),
              "pet_size_actual": pet.pet_size}
pet.status_bubble._do_show()
for _ in range(40):
    pump()
out["bubble"] = {"size": [pet.status_bubble.width(), pet.status_bubble.height()]}
from widgets import ModuleWidget                      # noqa: E402
ws = [w for w in pet.status_bubble.findChildren(ModuleWidget) if w.isVisible()]
out["bubble_widgets"] = [[type(w).__module__, w.width(), w.height()] for w in ws]
pet.close()

# ---- 5. 纯数值函数 ----
out["kit"] = {
    # UI_BASE 已经删掉（那 1.5 烘进源码了），现在看的是应用默认字号
    "APP_FONT_PT": kit.APP_FONT_PT,
    "bubble_k": round(kit.bubble_k(), 3),
    "pet_k": round(kit.pet_k(), 3),
    "row_height": kit.row_height(),
    "action_height": kit.bubble_token("action_height"),
    "bubble_width": kit.bubble_token("width"),
    "widget_width": kit.bubble_widget_width(),
    "ui_510": kit.ui(510),
    "ui_192": kit.ui(192),
    "ui_i_39": kit.ui_i(39),
    "bs_15": kit.bs(15),
    "ps_15": kit.ps(15),
}
out["pet_shadow_margin"] = G.PET_SHADOW_MARGIN
out["popup_font_px"] = None
try:
    import status_monitor as sm
    from PyQt5.QtGui import QFontMetrics
    f = QFont("Microsoft YaHei")
    f.setPointSizeF(13.5 * kit.pet_k())   # 13.5 = 旧版的 9 × 1.5，已烘进源码
    out["popup_font_px"] = QFontMetrics(f).height()
except Exception:
    pass

print(json.dumps(out, ensure_ascii=False, indent=1))

if "--save" in sys.argv:
    open(sys.argv[sys.argv.index("--save") + 1], "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("\n[已保存基准]")
elif "--cmp" in sys.argv:
    base = json.load(open(sys.argv[sys.argv.index("--cmp") + 1], encoding="utf-8"))
    diffs = []

    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a) | set(b):
                walk(a.get(k), b.get(k), path + "/" + str(k))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, path + "[%d]" % i)
        elif a != b:
            diffs.append("%s: 基准 %r → 现在 %r" % (path, a, b))

    walk(base, out)
    print("\n不一致 %d 处" % len(diffs))
    for d in diffs[:60]:
        print("  " + d)
    sys.exit(1 if diffs else 0)
