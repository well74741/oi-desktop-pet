# -*- coding: utf-8 -*-
"""手动验真：热键录入框在"组合键被别的程序占着"时也能录进去（真实系统按键）。

复现的是用户反馈："Ctrl+Alt+Space 前两个键能识别到，空格识别不到"——别的程序
RegisterHotKey 占了这个组合，系统在按键送达任何窗口之前就把它截走了。
这里用另一个线程扮演"占着组合的程序"，再用 SendInput 发**真实**按键。

**会往前台窗口发真实按键**，所以不进自动测试（打包时如果你在打字会被干扰）。
用户数据进临时沙箱。用法：python _check_hotkey_capture.py
期望：录入框 = Ctrl+Alt+Space、旁边提示"已被其他程序占用"、占着的程序没被触发、
焦点离开后钩子摘掉。
"""
import ctypes, ctypes.wintypes as wt, os, sys, time, tempfile, shutil, faulthandler
faulthandler.enable()
os.environ.pop("QT_QPA_PLATFORM", None)
D = "D:/@AItest/vibe coding/oi桌宠"
sys.path.insert(0, D); os.chdir(D)
import data_store
SAND = tempfile.mkdtemp(prefix="oi_hkreal_"); data_store.DATA_DIR = SAND
import pet_gravity as G
assert SAND.lower() in G.get_config_path().lower()
from PyQt5.QtWidgets import QApplication
app = QApplication([])
from widgets import kit
kit.install_ui_zoom(app)
u = ctypes.windll.user32
class KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class _U(ctypes.Union):
    _fields_ = [("ki", KI), ("pad", ctypes.c_byte * 32)]
class INP(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]
def key(vk, up=False):
    i = INP(type=1, u=_U(ki=KI(wVk=vk, wScan=u.MapVirtualKeyW(vk, 0), dwFlags=2 if up else 0)))
    u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))
def pump(s):
    t = time.time()
    while time.time() - t < s:
        app.processEvents(); time.sleep(0.01)
pet = G.GravityPet(G.load_settings()); pet.move(-7000, -7000); pet.show(); pump(0.5)
d = G.SettingsDialog(pet.settings, pet=pet); d.show(); d.raise_(); d.activateWindow()
u.SetForegroundWindow(int(d.winId())); pump(0.6)
import threading
_ready = threading.Event(); _hold = {}
def _other_app():
    _hold["ok"] = bool(u.RegisterHotKey(None, 0x77, 0x0003 | 0x4000, 0x20))
    _hold["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
    _ready.set()
    m = wt.MSG()
    while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        if m.message == 0x0312: _hold["fired"] = True
        if m.message == 0x8011: break
    u.UnregisterHotKey(None, 0x77)
threading.Thread(target=_other_app, daemon=True).start(); _ready.wait(2)
print("other app holds Ctrl+Alt+Space:", _hold.get("ok"), flush=True)
d.hk_edit.setFocus(); pump(0.3)
seen = []
orig = d.hk_edit.keyPressEvent
def _kp(ev):
    seen.append(hex(ev.key()))
    orig(ev)
d.hk_edit.keyPressEvent = _kp
fw = app.focusWidget()
for vk, up in ((0x11, 0), (0x12, 0), (0x20, 0), (0x20, 1), (0x12, 1), (0x11, 1)):
    key(vk, up); pump(0.05)
pump(0.4)
ok = []
def check(n, c, x=""):
    ok.append(c); print(("PASS " if c else "FAIL ") + n + ("  " + x if x else ""), flush=True)
check("模拟的「别的程序」确实占着 Ctrl+Alt+Space", bool(_hold.get("ok")))
check("录入框录到了 Ctrl+Alt+Space", d.hk_edit.text() == "Ctrl+Alt+Space", repr(d.hk_edit.text()))
check("当场提示被占用", d.hk_warn.isVisible() and "占用" in d.hk_warn.text(), repr(d.hk_warn.text()))
check("占着它的程序没被触发（按键被吞掉了）", not _hold.get("fired"))
d.hk_edit.clearFocus(); pump(0.2)
check("焦点离开后键盘钩子已摘掉", not d.hk_edit._hook)
u.PostThreadMessageW(_hold["tid"], 0x8011, 0, 0)
d.close(); pet.close(); pump(0.2)
shutil.rmtree(SAND, ignore_errors=True)
print("\n通过 %d，失败 %d" % (sum(ok), len(ok) - sum(ok)))
sys.exit(0 if all(ok) else 1)
