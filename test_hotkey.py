# -*- coding: utf-8 -*-
"""热键录入框 + 设置窗保存（自动测试）。

运行：python test_hotkey.py（离屏；用户数据进临时沙箱）。

覆盖两个用户实际碰到 / 差点碰到的问题：
1) "快捷键设置似乎无法填入组合键"：原来是普通文本框，**按**组合键不会输入任何字，
   只能一个字母一个字母打出 "Ctrl+Alt+Space"。现在是 HotkeyEdit，按下即录。
2) 设好热键后再改任何设置点「确定」，会报"已被其他程序占用"、存不下来 —— 保存时
   会试注册一次，而占着那个组合的正是桌宠自己。

会**真的注册**一个全局热键（Ctrl+Alt+Shift+F11，几乎不会和别的软件冲突），
结束时注销。按真实按键唤出菜单这一步仍需人工确认（合成按键不触发 WM_HOTKEY）。
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data_store                                    # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_hotkey_test_")
data_store.DATA_DIR = SAND
import pet_gravity as G                              # noqa: E402

_cfg = G.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

from PyQt5.QtCore import QEvent, Qt                  # noqa: E402
from PyQt5.QtGui import QKeyEvent                    # noqa: E402
from PyQt5.QtTest import QTest                       # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget    # noqa: E402

app = QApplication([])
import hotkey as H                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


CA = Qt.ControlModifier | Qt.AltModifier
COMBO = "Ctrl+Alt+Shift+F11"
try:
    # ===== 一、录入框 =====
    print("--- 一、按下即录 ---")
    caps = []
    e = H.HotkeyEdit(on_capture=caps.append)
    e.show()
    e.setFocus()
    app.processEvents()
    QTest.keyClick(e, Qt.Key_Space, CA)
    check("按下 Ctrl+Alt+Space → 录成 Ctrl+Alt+Space", e.text() == "Ctrl+Alt+Space", repr(e.text()))
    QTest.keyClick(e, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
    check("再按 Ctrl+Shift+K → 覆盖成新的", e.text() == "Ctrl+Shift+K", repr(e.text()))
    QTest.keyClick(e, Qt.Key_F12, Qt.AltModifier)
    check("F 键也认（Alt+F12）", e.text() == "Alt+F12", repr(e.text()))
    QTest.keyClick(e, Qt.Key_QuoteLeft, CA)
    check("符号键也认（Ctrl+Alt+`）", e.text() == "Ctrl+Alt+`", repr(e.text()))
    check("录下来的每一种写法都能被 parse 认出来（存进设置后真能注册）",
          all(H.parse(t) != (0, 0) for t in
              ("Ctrl+Alt+Space", "Ctrl+Shift+K", "Alt+F12", "Ctrl+Alt+`")))

    QTest.keyPress(e, Qt.Key_Control, Qt.ControlModifier)
    QTest.keyPress(e, Qt.Key_Alt, CA)
    check("只按住修饰键时先预览 Ctrl+Alt+…", e.text() == "Ctrl+Alt+…", repr(e.text()))
    QTest.keyRelease(e, Qt.Key_Alt, Qt.ControlModifier)
    QTest.keyRelease(e, Qt.Key_Control, Qt.NoModifier)
    check("没按主键就松开 → 恢复原来那个", e.text() == "Ctrl+Alt+`", repr(e.text()))

    QTest.keyClick(e, Qt.Key_A, Qt.NoModifier)
    check("不带修饰键的单键不录（太容易和正在用的程序打架）", e.text() == "Ctrl+Alt+`",
          repr(e.text()))
    QTest.keyClick(e, Qt.Key_Backspace, Qt.NoModifier)
    check("Backspace = 清空（不用热键）", e.text() == "", repr(e.text()))

    ev = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_A, Qt.AltModifier)
    e.event(ev)
    check("Alt+字母 不会先被对话框快捷键吃掉（拦下 ShortcutOverride）", ev.isAccepted())

    w2 = QWidget()
    w2.show()
    w2.setFocus()
    e.clearFocus()
    app.processEvents()
    check("进出焦点时通知调用方（用来暂停 / 恢复桌宠自己的热键）",
          True in caps and caps[-1] is False, str(caps))

    # ===== 二、暂停 / 恢复桌宠的热键 =====
    print("\n--- 二、录入时暂停桌宠热键 ---")

    class Pet(QWidget):
        pass

    pet = Pet()
    pet.settings = {"menu_hotkey": COMBO}
    pet._hotkey = None
    pet._hotkey_spec = ""
    for name in ("_apply_hotkey", "pause_hotkey", "active_hotkey", "_on_hotkey",
                 "_menu_anchor"):
        setattr(pet, name, getattr(G.GravityPet, name).__get__(pet))
    pet._apply_hotkey()
    reg = bool(pet._hotkey and pet._hotkey.registered())
    check("桌宠注册了热键 %s" % COMBO, reg)
    if not reg:
        raise SystemExit("这台机器上 %s 被占用，后面的用例无法进行" % COMBO)
    probe = H.GlobalHotkey()
    check("对照：桌宠占着时，别人再注册同一个组合会失败（这正是误报的来源）",
          not probe.register(COMBO)[0])
    probe.unregister()
    pet.pause_hotkey(True)
    probe = H.GlobalHotkey()
    ok_paused = probe.register(COMBO)[0]
    probe.unregister()
    check("暂停后系统里不再占着这个组合（录入框能收到按键）", ok_paused)
    check("暂停期间 active_hotkey 仍报告这个组合（保存时据此跳过试注册）",
          pet.active_hotkey() == COMBO)
    pet.pause_hotkey(False)
    check("恢复后重新占上", bool(pet._hotkey and pet._hotkey.registered()))

    # ===== 三、设置窗保存：不再误报"被占用" =====
    print("\n--- 三、设置窗保存 ---")
    warns = []
    real_warn = G._kit.warn
    G._kit.warn = lambda parent, title, text: warns.append((title, text))
    st = G.load_settings()
    st["menu_hotkey"] = COMBO
    d = G.SettingsDialog(st, pet=pet)
    accepted = []
    d.accepted.connect(lambda: accepted.append(1))
    d._on_accept()
    check("热键没改、桌宠正占着它 → 点「确定」能保存（以前会报被占用）",
          accepted and not warns, "弹窗=%s" % warns)
    check("保存后桌宠的热键还在（关窗口会恢复暂停）",
          bool(pet._hotkey and pet._hotkey.registered()))

    warns.clear()
    other = H.GlobalHotkey()
    other_combo = "Ctrl+Alt+Shift+F10"
    held = other.register(other_combo)[0]
    if held:
        d2 = G.SettingsDialog(G.load_settings(), pet=pet)
        d2.hk_edit.setText(other_combo)
        d2._on_accept()
        check("换成一个真被别人占着的组合 → 仍会如实提示（没把检查整个关掉）",
              warns and "用不了" in warns[0][0], str(warns))
        other.unregister()
        try:
            d2.close()
        except Exception:
            pass
    G._kit.warn = real_warn
finally:
    try:
        if pet._hotkey is not None:
            pet._hotkey.unregister()
    except Exception:
        pass
    shutil.rmtree(SAND, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
