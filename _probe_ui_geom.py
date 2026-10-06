# -*- coding: utf-8 -*-
"""几何预言机：把每个窗口里**所有**子控件的尺寸/字号导成 JSON，逐项比对。

为什么需要它：取消全局 1.5 倍率涉及 ~285 个调用点 + ~459 处 QSS px，其中一部分
参数是变量（bs(FIX_H)、kit.ui(PANEL_W)）或者位于"本来就没被放大"的区域
（气泡/桌宠标了 oi_nozoom）。所以不能靠肉眼或规则推理判断对错，
只有把改造前后每个控件的实测尺寸逐项对齐，才能证明"显示效果没变"。

用法：
    python _probe_ui_geom.py --save 基线.json     # 改造前跑（需要先 git stash 还原）
    python _probe_ui_geom.py --cmp  基线.json     # 改造后跑，逐项比对
"""
import json
import os
import sys
import tempfile
import time

os.environ.pop("QT_QPA_PLATFORM", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import data_store                                     # noqa: E402

SANDBOX = tempfile.mkdtemp(prefix="oi_geom_")
data_store.DATA_DIR = SANDBOX

from PyQt5.QtCore import Qt                           # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget     # noqa: E402

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication([])

from widgets import kit                               # noqa: E402

try:
    kit.install_ui_zoom(app)
except Exception:
    pass

import module_editor as ME                            # noqa: E402
import module_templates as mt                         # noqa: E402
import pet_gravity as G                               # noqa: E402
import updater                                        # noqa: E402
import update_ui                                      # noqa: E402

out = {}


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.002)


def walk(w, prefix, acc, depth=0):
    """递归记录一个控件：尺寸约束 / 实测尺寸 / 字号。名字用 类名#objectName#序号。"""
    try:
        kids = w.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
    except Exception:
        kids = []
    # 直接子控件的顺序在 Qt 里是稳定的（构造顺序）
    for i, c in enumerate(kids):
        try:
            name = "%s#%s#%d" % (type(c).__name__, c.objectName() or "-", i)
        except Exception:
            name = "widget#-#%d" % i
        key = prefix + "/" + name
        f = c.font()
        rec = {
            "size": [c.width(), c.height()],
            "min": [c.minimumWidth(), c.minimumHeight()],
            "max": [c.maximumWidth(), c.maximumHeight()],
            "hint": [c.sizeHint().width(), c.sizeHint().height()],
            "font_px": f.pixelSize(),
            "font_pt": round(f.pointSizeF(), 2),
        }
        lay = c.layout()
        if lay is not None:
            m = lay.contentsMargins()
            rec["margins"] = [m.left(), m.top(), m.right(), m.bottom()]
            rec["spacing"] = lay.spacing()
        acc[key] = rec
        if depth < 8:
            walk(c, key, acc, depth + 1)


def attrs(win_name, w):
    """自绘窗口（径向菜单）没有子控件，只能记录它自己算出来的几何量。"""
    rec = {}
    for k in ("_margin", "_icon_size", "_sector_scale", "_arc_start", "_arc_sweep",
              "pet_size", "_inside_ratio", "_drag_room", "is_visible_state",
              "_anim_duration", "_stagger_delay"):
        v = getattr(w, k, None)
        if isinstance(v, (int, float, bool)) or v is None:
            rec[k] = v
    try:
        rec["_sector_outer"] = round(float(w._sector_outer()), 3)
    except Exception:
        pass
    for k in ("_target_positions", "_btn_anim_state", "_anim_rank", "buttons"):
        v = getattr(w, k, None)
        if v is not None:
            try:
                rec["len" + k] = len(v)
            except Exception:
                pass
    ps_ = getattr(w, "pet", None)
    if ps_ is not None:
        for k in ("pet_size", "pet_opacity"):
            v = getattr(ps_, k, None)
            if isinstance(v, (int, float)):
                rec["pet." + k] = v
    return rec


def kit_scalars():
    """缩放函数的输出值：这些是"最终像素"，必须逐一相同。"""
    out = {}
    from widgets import kit
    out["bubble_k"] = round(kit.bubble_k(), 4)
    out["pet_k"] = round(kit.pet_k(), 4)
    for n in range(1, 25):
        out["bs(%d)" % n] = kit.bs(n)
        out["ps(%d)" % n] = kit.ps(n)
    for sz in (7.5, 8, 10, 11, 12):
        try:
            f = kit.font_pt(sz)
            out["font_pt(%s)" % sz] = [f.pixelSize(), round(f.pointSizeF(), 3)]
        except Exception:
            pass
    for nm, h in (("row_height", kit.row_height()),
                  ("caption_height", kit.caption_height()),
                  ("toolbar_height", kit.toolbar_height()),
                  ("header_row_height", kit.header_row_height(True)),
                  ("widget_width", kit.bubble_widget_width())):
        out[nm] = h
    for tok in sorted(kit.BUBBLE_TOKENS):
        out["token." + tok] = kit.bubble_token(tok)
    return out


def snap(win_name, w, settle=0.3):
    try:
        w.move(-9000, -9000)
        w.show()
        t = time.time()
        while time.time() - t < settle:
            app.processEvents()
            time.sleep(0.01)
        acc = {}
        walk(w, win_name, acc)
        out[win_name] = {"self": [w.width(), w.height()], "kids": acc,
                         "attrs": attrs(win_name, w)}
        return len(acc)
    except Exception as e:
        out[win_name] = {"error": repr(e)}
        return 0


shown = []
sd = G.SettingsDialog(G.load_settings())
shown.append(("settings", sd, snap("settings", sd)))
try:
    sd._switch_rules_tab("custom")
    shown.append(("settings_added", sd, snap("settings_added", sd)))
    sd._switch_rules_tab("builtin")
except Exception:
    pass

e = ME.ModuleEditor(None, None, {"ai_profile": {}})
shown.append(("editor_add", e, snap("editor_add", e)))
e.close()
e = ME.ModuleEditor(None, mt.BY_KEY["countdown"].build("下班倒计时", {}), {"ai_profile": {}})
shown.append(("editor_edit", e, snap("editor_edit", e)))
e.close()
e = ME.ModuleEditor(None, mt.BY_KEY["script"].build("脚本", {}), {"ai_profile": {}})
try:
    e.adv_btn.setChecked(True)
except Exception:
    pass
shown.append(("editor_adv", e, snap("editor_adv", e)))
e.close()

_mode = updater.install_mode
updater.install_mode = lambda: "installed"
try:
    u = update_ui.UpdateDialog(None, None, {
        "version": "9.9.9", "tag": "v9.9.9", "title": "v9.9.9",
        "notes": "## 改动\n- 第一条\n- 第二条\n\n正文一段。",
        "asset": {"name": "oi-pet_Setup_v9.9.9.exe", "size": 21238080}})
    shown.append(("update", u, snap("update", u, 0.4)))
    u.close()
finally:
    updater.install_mode = _mode

try:
    md = kit._MsgDialog(None, "确认一下", "这是一个提示弹窗，用来看字号和内边距。",
                        confirm_mode=True)
    shown.append(("msg", md, snap("msg", md)))
    md.close()
except Exception as e:
    out["msg"] = {"error": repr(e)}

# 桌宠 / 气泡 / 径向菜单（含组件）
try:
    st = G.load_settings()
    st["slot_shortcuts"] = [{"name": "记事本", "path": "notepad.exe"},
                            {"name": "计算器", "path": "calc.exe"},
                            {"name": "画图", "path": "mspaint.exe"},
                            {"name": "资源管理器", "path": "explorer.exe"}]
    st["status_rules"] = [dict(mt.BY_KEY[k].build(k), id="t_" + k)
                          for k in ("counter", "notes", "perler", "todo", "stats",
                                    "tokenmeter", "tomato", "health", "launcher", "calc")]
    G.save_settings(st)
    pet = G.GravityPet(G.load_settings())
    pet.move(-3000, 300)
    pet.show()
    pump(30)
    shown.append(("pet", pet, snap("pet", pet, 0.5)))
    pet.status_bubble._do_show()
    pump(80)
    shown.append(("bubble", pet.status_bubble, snap("bubble", pet.status_bubble, 0.5)))
    pet.radial_menu.toggle_menu(list(pet.settings.get("slot_shortcuts", [])))
    pump(80)
    shown.append(("radial", pet.radial_menu, snap("radial", pet.radial_menu, 0.5)))
    pet.close()
    pump(10)
except Exception as e:
    out["pet"] = {"error": repr(e)}

out["_kit"] = kit_scalars()

for name, _w, n in shown:
    print("%-16s 记录 %d 个控件" % (name, n))

print("\n控件总数：%d" % sum(len(v.get("kids", {})) for v in out.values()))

if "--save" in sys.argv:
    p = sys.argv[sys.argv.index("--save") + 1]
    open(p, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print("[已保存] %s" % p)
elif "--cmp" in sys.argv:
    base = json.load(open(sys.argv[sys.argv.index("--cmp") + 1], encoding="utf-8"))
    diffs = []
    for win in sorted(set(base) | set(out)):
        b, n = base.get(win, {}), out.get(win, {})
        if b.get("self") != n.get("self"):
            diffs.append("%s 窗口尺寸 %s → %s" % (win, b.get("self"), n.get("self")))
        ba, na = b.get("attrs", {}) or {}, n.get("attrs", {}) or {}
        for k in sorted(set(ba) | set(na)):
            if ba.get(k) != na.get(k):
                diffs.append("%s 属性 %s %s → %s" % (win, k, ba.get(k), na.get(k)))
        bk, nk = b.get("kids", {}), n.get("kids", {})
        for k in sorted(set(bk) | set(nk)):
            if k not in nk:
                diffs.append("%s %s 消失" % (win, k))
            elif k not in bk:
                diffs.append("%s %s 新增" % (win, k))
            elif bk[k] != nk[k]:
                for f in ("size", "min", "max", "hint", "font_px", "font_pt",
                          "margins", "spacing"):
                    if bk[k].get(f) != nk[k].get(f):
                        diffs.append("%s %s .%s %s → %s"
                                     % (win, k, f, bk[k].get(f), nk[k].get(f)))
    print("\n不一致 %d 项" % len(diffs))
    cap = 10000 if "--all" in sys.argv else 80
    for d in diffs[:cap]:
        print("  " + d)
    if len(diffs) > cap:
        print("  …还有 %d 项" % (len(diffs) - cap))
    sys.exit(1 if diffs else 0)
