# -*- coding: utf-8 -*-
"""把所有对话框里每个布局（含 addLayout 挂上去的嵌套布局和固定空白）的边距/间距
打平成文本，两棵工作树各跑一次再 diff。

为什么需要：旧版整树放大有个"继承判定"——子布局的间距恰好和父布局相等时，会被当成
"没显式设过"而跳过放大。烘焙器不知道这个坑，照乘了 1.5，于是那几行多出 3px。
这类偏差尺寸全对、只是位置挪了，几何预言机（只记尺寸）看不见，只能两边实测对比。

用法：OIROOT=<工作树> python t_lay.py
"""
import os
import sys
import tempfile

os.environ.pop("QT_QPA_PLATFORM", None)
HERE = os.environ["OIROOT"]
sys.path.insert(0, HERE)
os.chdir(HERE)

import data_store                                     # noqa: E402

data_store.DATA_DIR = tempfile.mkdtemp(prefix="oi_lay_")

from PyQt5.QtWidgets import QApplication              # noqa: E402

app = QApplication([])
from widgets import kit                               # noqa: E402
import module_templates as mt                         # noqa: E402
import module_editor as ME                            # noqa: E402
import pet_gravity as G                               # noqa: E402
import update_ui                                      # noqa: E402
import updater                                        # noqa: E402

kit.install_ui_zoom(app)


def dump(root, tag):
    out = []

    def lay_line(lay, path):
        m = lay.contentsMargins()
        out.append("%-70s %-13s m=[%d,%d,%d,%d] sp=%d"
                   % (path[-70:], type(lay).__name__, m.left(), m.top(),
                      m.right(), m.bottom(), lay.spacing()))
        for i in range(lay.count()):
            it = lay.itemAt(i)
            if it is None:
                continue
            sub = it.layout()
            if sub is not None:
                lay_line(sub, "%s>L%d" % (path, i))
                continue
            sp = it.spacerItem()
            if sp is not None:
                sh = sp.sizeHint()
                out.append("%-70s %-13s spacer=%dx%d"
                           % (("%s>S%d" % (path, i))[-70:], "-",
                              sh.width(), sh.height()))

    def walk(w, path):
        lay = w.layout()
        if lay is not None:
            lay_line(lay, path)
        for i, c in enumerate([c for c in w.children() if c.isWidgetType()]):
            walk(c, "%s/%s#%s#%d" % (path, type(c).__name__,
                                     c.objectName() or "-", i))

    walk(root, tag)
    print("\n".join(out))


def pump(n):
    for _ in range(n):
        app.processEvents()


d = G.SettingsDialog(G.load_settings())
d.show()
pump(80)
dump(d, "settings")
try:
    d._switch_rules_tab("custom")
    pump(40)
    dump(d, "settings_added")
except Exception:
    pass
d.close()

a = G.AISettingsDialog(G.load_settings())
a.show()
pump(60)
dump(a, "ai")
a.close()

for tag, rule in (("ed_add", None),
                  ("ed_edit", mt.BY_KEY["countdown"].build("x", {})),
                  ("ed_adv", mt.BY_KEY["script"].build("s", {}))):
    e = ME.ModuleEditor(None, rule, {"ai_profile": {}})
    if tag == "ed_adv":
        try:
            e.adv_btn.setChecked(True)
        except Exception:
            pass
    e.show()
    pump(50)
    dump(e, tag)
    e.close()

updater.install_mode = lambda: "installed"
u = update_ui.UpdateDialog(None, None, {
    "version": "9.9.9", "tag": "v9.9.9", "title": "v9.9.9",
    "notes": "## a\n- b\n\nc.", "asset": {"name": "x.exe", "size": 21238080}})
u.show()
pump(60)
dump(u, "update")

m = kit._MsgDialog(None, "ti", "zheng wen", confirm_mode=True)
m.show()
pump(40)
dump(m, "msg")

sys.stdout.flush()
os._exit(0)
