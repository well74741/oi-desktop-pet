# -*- coding: utf-8 -*-
"""逐窗截图：改造前 / 改造后各跑一次，用 `_cmp_shots.py` 逐像素比对。

为什么要有这个：界面缩放改造（去掉全局 1.5 倍率、把数值直接烤进源码）涉及约 500 个
数值和整个缩放子系统，肉眼根本看不过来。只有把每个窗口都截下来逐像素比，才能说
"显示效果没变"。任何一个窗口不一样，就是漏改或多改了数值。

用法：python _shot_ui.py --out <目录>      用户数据进临时沙箱。
"""
import os
import sys
import tempfile
import time

os.environ.pop("QT_QPA_PLATFORM", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import data_store                                     # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_shot_")
data_store.DATA_DIR = SAND

from PyQt5.QtCore import Qt                           # noqa: E402
from PyQt5.QtWidgets import QApplication              # noqa: E402

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication([])

from widgets import kit                               # noqa: E402

kit.install_ui_zoom(app)

import module_editor as ME                            # noqa: E402
import module_templates as mt                         # noqa: E402
import pet_gravity as G                               # noqa: E402
import updater                                        # noqa: E402
import update_ui                                      # noqa: E402

OUT = "ui_shots"
if "--out" in sys.argv:
    OUT = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(OUT, exist_ok=True)
WARN = []


def pump(sec=0.25):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.01)


def grab(name, w, settle=0.25):
    """把窗口挪到屏幕外再截：不影响用户桌面，几何也稳定。"""
    try:
        w.move(-9000, -9000)
        w.show()
        pump(settle)
        pm = w.grab()
        pm.save(os.path.join(OUT, name + ".png"))
        print("%-22s %4d x %-4d" % (name, pm.width(), pm.height()))
    except Exception as e:
        WARN.append("%s: %r" % (name, e))
        print("%-22s 跳过（%r）" % (name, e))


# ---- 设置窗（三个页面）----
sd = G.SettingsDialog(G.load_settings())
grab("settings_default", sd)
try:
    sd._switch_rules_tab("custom")
    grab("settings_added_tab", sd)
    sd._switch_rules_tab("builtin")
except Exception as e:
    WARN.append("rules_tab: %r" % e)
try:
    ai = G.AISettingsDialog(G.load_settings(), sd)
    grab("ai_settings", ai)
    ai.close()
except Exception as e:
    WARN.append("ai_settings: %r" % e)
sd.close()

# ---- 模块编辑窗（添加 / 编辑 / 高级展开）----
e = ME.ModuleEditor(None, None, {"ai_profile": {}})
grab("editor_add", e)
e.close()
e = ME.ModuleEditor(None, mt.BY_KEY["countdown"].build("下班倒计时", {}), {"ai_profile": {}})
grab("editor_edit", e)
e.close()
e = ME.ModuleEditor(None, mt.BY_KEY["script"].build("脚本", {}), {"ai_profile": {}})
try:
    e.adv_btn.setChecked(True)
except Exception:
    pass
grab("editor_advanced", e)
e.close()

# ---- 检查更新窗 ----
_mode = updater.install_mode
updater.install_mode = lambda: "installed"
try:
    u = update_ui.UpdateDialog(None, None, {
        "version": "9.9.9", "tag": "v9.9.9", "title": "v9.9.9",
        "notes": "## 改动\n- 第一条\n- 第二条\n\n正文一段。",
        "asset": {"name": "oi-pet_Setup_v9.9.9.exe", "size": 21238080}})
    grab("update_dialog", u, settle=0.4)
    u.close()
finally:
    updater.install_mode = _mode

# ---- 消息弹窗 ----
try:
    md = kit._MsgDialog(None, "确认一下", "这是一个提示弹窗，用来看字号和内边距。",
                        confirm_mode=True)
    grab("msg_dialog", md)
    md.close()
except Exception as e:
    WARN.append("msg_dialog: %r" % e)

# ---- 桌宠 / 气泡 / 径向菜单 ----
try:
    st = G.load_settings()
    st["slot_shortcuts"] = [{"name": "记事本", "path": "notepad.exe"},
                            {"name": "计算器", "path": "calc.exe"},
                            {"name": "画图", "path": "mspaint.exe"},
                            {"name": "资源管理器", "path": "explorer.exe"}]
    G.save_settings(st)
    pet = G.GravityPet(G.load_settings())
    pet.move(-3000, 300)
    pet.show()
    pump(0.6)
    grab("pet", pet, settle=0.2)
    pet.status_bubble._do_show()
    pump(1.4)
    grab("bubble", pet.status_bubble, settle=0.2)
    pet.radial_menu.toggle_menu(list(pet.settings.get("slot_shortcuts", [])))
    pump(1.4)
    grab("radial_menu", pet.radial_menu, settle=0.2)
    pet.close()
    pump(0.3)
except Exception as e:
    WARN.append("pet: %r" % e)

print("\n截图目录：%s" % os.path.abspath(OUT))
if WARN:
    print("有 %d 项没能截到：" % len(WARN))
    for w in WARN:
        print("  " + w)
