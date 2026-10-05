# -*- coding: utf-8 -*-
"""验真：全局热键（解析 / 注册 / 注销 / 线程生命周期 / 设置往返）。

**一件必须说清楚的事**：本脚本**无法验证"真人按下组合键"**。开发环境里合成的
按键（keybd_event / SendInput）不会触发 WM_HOTKEY —— 实测过：注册成功、消息循环
正常，用 PostThreadMessage 往里投一条 WM_HOTKEY 能收到，但合成按键一条都进不来。
所以"按下热键真的能唤出菜单"这一步只能人工确认，脚本不假装测过。

能自动覆盖的（都在这儿）：
- 组合键解析与规范化（各种写法、错误写法）
- 真注册 / 真注销（真的是 Win32 的 RegisterHotKey）
- 重复注册同一组合键会失败且**如实报错**（不能静默失败）
- 注册 → 注销 → 再注册 能来回（线程不泄漏）
- 线程生命周期：注销后线程要结束，不能挂着一个死线程
- 设置往返：写进 settings 再读回来

用法：python _check_hotkey.py
"""
import os
import shutil
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_store                                    # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_hotkey_")
data_store.DATA_DIR = SAND
import pet_gravity                                   # noqa: E402

_cfg = pet_gravity.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

from PyQt5.QtWidgets import QApplication             # noqa: E402

app = QApplication([])
import hotkey                                        # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


# ---------- 一、解析 ----------
print("--- 一、组合键解析 ---")
cases = [
    ("Ctrl+Alt+Space", (hotkey.MOD_CONTROL | hotkey.MOD_ALT, 0x20)),
    ("ctrl+shift+k", (hotkey.MOD_CONTROL | hotkey.MOD_SHIFT, ord("K"))),
    ("Alt+F12", (hotkey.MOD_ALT, 0x7B)),
    ("Win+Alt+1", (hotkey.MOD_WIN | hotkey.MOD_ALT, ord("1"))),
    ("Ctrl+Alt+`", (hotkey.MOD_CONTROL | hotkey.MOD_ALT, 0xC0)),
    ("Ctrl + Alt + Space", (hotkey.MOD_CONTROL | hotkey.MOD_ALT, 0x20)),
]
ok = True
for spec, want in cases:
    got = hotkey.parse(spec)
    if got != want:
        ok = False
        print("     解析错：%r -> %s，期望 %s" % (spec, got, want))
check("常见写法都能解析对（含空格分隔、符号键、F 键、Win 键）", ok)

bad = ["", "space", "Ctrl", "Ctrl+", "Ctrl+Alt+A+B", "Ctrl+Alt+乱码", "F12"]
ok = all(hotkey.parse(x) == (0, 0) for x in bad)
check("单键 / 缺主键 / 多主键 / 乱码 一律判为不合法（不静默接受）", ok,
      "检查了 %s" % bad)

check("规范化：大小写与顺序统一成 Ctrl+Alt+Space",
      hotkey.normalize("ctrl+alt+space") == "Ctrl+Alt+Space"
      and hotkey.normalize("ALT+CTRL+SPACE") == "Ctrl+Alt+Space",
      "%r / %r" % (hotkey.normalize("ctrl+alt+space"),
                   hotkey.normalize("ALT+CTRL+SPACE")))

# ---------- 二、真注册 / 真注销 ----------
print("\n--- 二、真实注册与注销 ---")
check("Windows 上 supported() 为真", hotkey.supported())

hk = hotkey.GlobalHotkey()
ok, msg = hk.register("Ctrl+Alt+Space")
check("能注册一个热键", ok, msg or "(无错误)")
check("注册后 registered() 为真", hk.registered())
tid1 = hk._tid
check("注册真的落到了某个线程上（拿到线程 id）", tid1 > 0, "tid=%d" % tid1)

# 处理链路：往那个线程投一条 WM_HOTKEY，看信号有没有发出来
got = []
hk.pressed.connect(lambda: got.append(1))
sent = False
try:
    sent = bool(hotkey.ctypes.windll.user32.PostThreadMessageW(
        tid1, hotkey.WM_HOTKEY, hotkey._HOTKEY_ID, 0))
except Exception:
    pass
deadline = time.monotonic() + 3
while time.monotonic() < deadline and not got:
    app.processEvents()
    time.sleep(0.01)
check("投递 WM_HOTKEY 后 pressed 信号被发出（处理链路通）",
      sent and len(got) == 1,
      "投递=%s 收到=%d 次（注意：这不等于真人按键验证过）" % (sent, len(got)))

# ---------- 三、冲突要如实报错 ----------
print("\n--- 三、组合键冲突 ---")
hk2 = hotkey.GlobalHotkey()
ok2, msg2 = hk2.register("Ctrl+Alt+Space")
check("同一组合键被占用时注册失败", not ok2)
check("失败时给出可读提示，而不是静默失败",
      bool(msg2) and ("占用" in msg2 or "失败" in msg2), "提示=%r" % msg2)
check("失败后 registered() 为假（不能假装注册上了）", not hk2.registered())

# ---------- 四、线程生命周期 ----------
print("\n--- 四、线程生命周期 ---")
th = hk._thread
hk.unregister()
check("注销后 registered() 为假", not hk.registered())
deadline = time.monotonic() + 3
while time.monotonic() < deadline and th is not None and th.is_alive():
    time.sleep(0.02)
check("注销后热键线程确实结束了（不残留死线程）",
      th is None or not th.is_alive(),
      "线程还活着=%s" % (th.is_alive() if th is not None else None))

ok3, _ = hk2.register("Ctrl+Alt+Space")
check("释放之后能重新注册（来回切换不卡死）", ok3)
hk2.unregister()

# ---------- 五、设置往返 ----------
print("\n--- 五、设置往返 ---")
st = pet_gravity.load_settings()
check("新设置项有默认值：menu_hotkey 为空、在鼠标处弹出为真",
      st.get("menu_hotkey") == "" and st.get("menu_hotkey_at_cursor") is True,
      "menu_hotkey=%r at_cursor=%r" % (st.get("menu_hotkey"),
                                       st.get("menu_hotkey_at_cursor")))
st["menu_hotkey"] = "Ctrl+Alt+K"
pet_gravity.save_settings(st)
back = pet_gravity.load_settings()
check("存进去能读回来", back.get("menu_hotkey") == "Ctrl+Alt+K",
      "读回 %r" % back.get("menu_hotkey"))

check("用户数据没有被测试改写（只动了沙箱）",
      os.path.abspath(pet_gravity.get_config_path()).lower()
      == os.path.join(SAND, "pet_settings.json").lower())

shutil.rmtree(SAND, ignore_errors=True)
print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
print("提醒：**真人按键**这一步本脚本测不了，需要人工按下组合键确认菜单弹出。")
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
