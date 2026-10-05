# -*- coding: utf-8 -*-
"""AI 助手改设置 ↔ 设置窗 的一致性测试。

回归的是一个真实 bug：AI 助手"把桌宠调大"改的是 `pet_size`（基准像素），
而设置窗里「桌宠大小」滑块显示的是 `pet_scale` 档位——桌宠确实变大了，
打开设置却还是旧档位。顺带两个同族隐患：
1) 设置窗持有 `dict(settings)` 快照，开着窗时 AI 改设置，点确定会把旧快照
   整份写回去，覆盖 AI 的改动；
2) `_level_index()` 对不在档位表里的值一律回 0，AI 设 1.3 会显示成"标准"。

运行：python test_settings_sync.py
**隔离方式：改 `data_store.DATA_DIR` 指向临时目录**，并在写任何东西之前断言
`pet_gravity.get_config_path()` 真的落在临时目录里——不满足就直接退出。
切 cwd **不管用**：`get_config_path()` 拿的是 `data_store.DATA_DIR`（脚本所在
目录的绝对路径），跟当前工作目录无关，这份测试第一版就是这么误写了用户真实
的 pet_settings.json。
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SAND = tempfile.mkdtemp(prefix="oi_setsync_test_")
os.environ["TEMP"] = os.environ["TMP"] = SAND
try:
    shutil.copy(os.path.join(HERE, "config.yaml"), os.path.join(SAND, "config.yaml"))
except Exception:
    pass

# ---- 隔离用户数据：所有用户数据都在 data_store.DATA_DIR 下 ----
import data_store                                   # noqa: E402

data_store.DATA_DIR = SAND
import pet_gravity as _pg_probe                     # noqa: E402

_cfg = _pg_probe.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    # 宁可不测，也绝不写用户真实设置
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)
print("沙箱配置路径:", _cfg)

from PyQt5.QtWidgets import QApplication            # noqa: E402

app = QApplication([])

import pet_gravity as G                             # noqa: E402
import status_monitor as SM                         # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def pump(n=25):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.02)


def level_name(dlg):
    return G.UI_SCALE_OPTIONS[dlg._pet_slider.value()][0]


st = G.load_settings()
st["pet_scale"] = 1.0
st["bubble_scale"] = 1.0
st["pet_size"] = 75
G.save_settings(st)
pet = G.GravityPet(G.load_settings())
pet.move(-7000, -7000)          # 摆到屏幕外，别在桌面上闪
pet.show()
app.processEvents()

# ---------- 1. 设置窗的「桌宠大小」就是 AI 能改的键 ----------
check("AI 可调键里有设置窗那两个档位滑块（pet_scale / bubble_scale）",
      "pet_scale" in SM._PET_SETTING_KEYS and "bubble_scale" in SM._PET_SETTING_KEYS)
check("档位吸附：1.3 吸到较大 1.25", SM._snap_level(1.3) == (1.25, "较大"))
check("档位吸附：1.9 吸到特大 2.0", SM._snap_level(1.9) == (2.0, "特大"))
check("档位吸附：乱值兜底到标准", SM._snap_level("x") == (1.0, "标准"))
check("档位吸附后设置窗一定认得（_level_index 不会回落到 0）",
      all(G.SettingsDialog._level_index(None, v) == i
          for i, (_n, v) in enumerate(G.UI_SCALE_OPTIONS)))

# ---------- 2. AI 改完之后打开设置：显示的是新值 ----------
out = SM._tool_pet_setting({"key": "pet_scale", "value": 1.5})
pump()
check("AI 改档位：工具回执带档位名", "大档" in out, out)
check("AI 改档位：内存 / 磁盘 / 实际渲染三者一致",
      abs(float(pet.settings.get("pet_scale")) - 1.5) < 1e-9
      and abs(float(G.load_settings().get("pet_scale")) - 1.5) < 1e-9
      and abs(G._kit.pet_scale() - 1.5) < 1e-9)
dlg = G.SettingsDialog(pet.settings, pet=pet)
check("AI 改完再打开设置：滑块显示新档位（这就是用户报的那个 bug）",
      level_name(dlg) == "大", "窗里=%s" % level_name(dlg))
dlg.close()
app.processEvents()

# ---------- 3. 设置窗开着时 AI 再改：窗里跟着变，且确定不覆盖 ----------
pet._settings_dlg = dlg2 = G.SettingsDialog(pet.settings, pet=pet)
before = level_name(dlg2)
SM._tool_pet_setting({"key": "pet_scale", "value": 2.0})
pump()
check("设置窗开着时 AI 改档位：窗里的滑块跟着动",
      before == "大" and level_name(dlg2) == "特大",
      "%s → %s" % (before, level_name(dlg2)))
dlg2._on_accept()
app.processEvents()
check("设置窗开着时 AI 改档位：点确定不会把 AI 的改动覆盖回去",
      abs(float(G.load_settings().get("pet_scale")) - 2.0) < 1e-9,
      "磁盘=%s" % G.load_settings().get("pet_scale"))
pet._settings_dlg = None

# ---------- 4. AI 加模块/按钮时，开着的设置窗不能把它们删掉 ----------
st = G.load_settings()
st["status_rules"] = [{"name": "旧模块", "source": {"type": "static", "text": "1"}}]
st["slot_shortcuts"] = [{"name": "旧按钮", "path": "C:\\Windows\\notepad.exe"}]
G.save_settings(st)
pet.settings.update(G.load_settings())
pet._settings_dlg = dlg3 = G.SettingsDialog(pet.settings, pet=pet)
# 模拟 AI 工具：写盘 + 同步内存（reload 命令走的就是这条路）
st = G.load_settings()
st["status_rules"] = list(st["status_rules"]) + [
    {"name": "AI加的模块", "source": {"type": "static", "text": "2"}}]
st["slot_shortcuts"] = list(st["slot_shortcuts"]) + [
    {"name": "AI加的按钮", "path": "C:\\Windows\\system32\\calc.exe"}]
G.save_settings(st)
pet.settings.update(st)
pet.sync_settings_dialog()
app.processEvents()
check("AI 加模块：开着的设置窗同步到了工作副本",
      any(r.get("name") == "AI加的模块" for r in dlg3.temp_status_rules))
check("AI 加按钮：开着的设置窗同步到了工作副本",
      any(s.get("name") == "AI加的按钮" for s in dlg3.slot_shortcuts))
dlg3._on_accept()
app.processEvents()
disk = G.load_settings()
check("AI 加的模块/按钮不会被设置窗的确定删掉",
      any(r.get("name") == "AI加的模块" for r in disk.get("status_rules", []))
      and any(s.get("name") == "AI加的按钮" for s in disk.get("slot_shortcuts", [])))
pet._settings_dlg = None

# ---------- 5. 打开设置前会合并磁盘上的最新设置 ----------
src = open(os.path.join(HERE, "pet_gravity.py"), encoding="utf-8").read()
blk = src[src.index("def _open_settings"):]
blk = blk[:blk.index("_pre_dlg_scales")]
check("打开设置窗前先合并磁盘最新设置（兜住只写盘的改动路径）",
      "self.settings.update(load_settings())" in blk)

# ---------- 6. 托盘是唯一常驻入口（桌宠不占任务栏） ----------
# 桌宠是 Qt.Tool 工具窗：不占任务栏、不进 Alt+Tab。代价是任务管理器只在
# 「后台进程」里列它，所以"设置 / 退出"必须能从系统托盘右键拿到。
from PyQt5.QtCore import Qt as _Qt                        # noqa: E402

# 注意：Qt.Tool 是复合标志（Popup|Dialog|Window），顶层窗口天然带 Qt.Window，
# 用 flags & Qt.Tool 判断永远为真。必须比窗口类型掩码。
_wtype = pet.windowFlags() & _Qt.WindowType_Mask
check("桌宠是工具窗：不在任务栏里占一格", _wtype == _Qt.Tool)
check("桌宠有窗口标题（进程工具里认得出来）", pet.windowTitle() == "oi桌宠")
check("没有留下「在任务栏显示」这种开关", "taskbar_visible" not in pet.settings)
check("桌宠不抢焦点", pet.testAttribute(_Qt.WA_ShowWithoutActivating))

_main_src = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
_tray_blk = _main_src[_main_src.index("def _setup_tray"):
                      _main_src.index("def _show_pet")]
for _item in ("设置", "显示桌宠", "隐藏桌宠", "回到中央", "退出"):
    check("托盘右键有「%s」" % _item, '"%s"' % _item in _tray_blk)
check("托盘「设置」接到了真正的开设置窗入口",
      "self._open_settings" in _tray_blk
      and "self.pet._open_settings()" in _main_src)
check("桌宠确实有 _open_settings（托盘调的就是它）",
      callable(getattr(pet, "_open_settings", None)))

try:
    pet.close()
except Exception:
    pass
app.processEvents()
shutil.rmtree(SAND, ignore_errors=True)
# ---------- 启动外部程序时不许把桌宠目录传下去 ----------
# 这是"装新版报 DeleteFile failed; code 5"的**真正根因**：子进程继承 CWD，
# Windows 的 DLL 搜索顺序会查当前目录，于是被桌宠启动的程序（用户实测是
# Photoshop）从 C:\Program Files\oi桌宠\_internal\ 里加载了 VCRUNTIME140.dll
# 并一直占着句柄 —— 所以"任务管理器里找不到桌宠"也照样装不上，占用者不是桌宠。
_pg_src = open(os.path.join(HERE, "pet_gravity.py"), encoding="utf-8").read()
_lb = _pg_src[_pg_src.index("    def _launch(self, path):"):
              _pg_src.index("    def _reshow_menu")]
check("启动程序：径向菜单每个 Popen 都带 cwd（%d 个 Popen / %d 个 cwd=）"
      % (_lb.count("subprocess.Popen("), _lb.count("cwd=cwd")),
      _lb.count("subprocess.Popen(") > 0
      and _lb.count("cwd=cwd") >= _lb.count("subprocess.Popen("))
check("启动程序：os.startfile 也被包成「临时切目录再切回」",
      "_startfile_outside" in _pg_src)
check("启动程序：给出的工作目录不会是桌宠自己的目录",
      os.path.normcase(G.RadialMenu._launch_cwd(
          os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "cmd.exe")) or "")
      == os.path.normcase(os.path.join(
          os.environ.get("SystemRoot", r"C:\Windows"), "System32")))
_wl_src = open(os.path.join(HERE, "webchat_launcher.py"), encoding="utf-8").read()
check("启动程序：起浏览器也传了 cwd（浏览器同样会占住 DLL）",
      "cwd=_cwd" in _wl_src)
_lc_src = open(os.path.join(HERE, "widgets", "launcher.py"),
               encoding="utf-8").read()
check("启动程序：启动器组件用同一套安全工作目录",
      "_safe_cwd" in _lc_src and _lc_src.count("cwd=cwd") >= 3)

# ---------- 安装包能强制退出旧版（装新版不再报"文件被占用"） ----------
# 用户实测：退出旧版后装新版，仍报 _internal\VCRUNTIME140.dll
# "DeleteFile failed; code 5 拒绝访问"。根因是 CloseApplications=no，
# 而单实例只有 QLockFile 文件锁 —— 安装程序看不见文件锁，连提示都给不出来。
_iss = open(os.path.join(HERE, "oi桌宠.iss"), encoding="utf-8").read()
_main_src = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()

check("安装包：允许关闭占用进程（CloseApplications=yes）",
      "CloseApplications=yes" in _iss
      and "CloseApplications=no" not in _iss)
check("安装包：有 taskkill 兜底（托盘进程 Restart Manager 常抓不到）",
      "taskkill.exe" in _iss and "PrepareToInstall" in _iss)
check("安装包：卸载前也先结束进程（否则删不干净）",
      "InitializeUninstall" in _iss)
check("安装包：只结束自己的 exe，绝不碰 msedge（会关掉用户正在用的网页）",
      "oi桌宠.exe" in _iss and "msedge" not in _iss.replace("绝不碰 msedge", ""))

# AppMutex 的名字必须和代码里建的那个**完全一致**，否则等于没加
import re as _re_mx
_m = _re_mx.search(r"AppMutex=(\S+)", _iss)
_n = _re_mx.search(r'APP_MUTEX_NAME = "([^"]+)"', _main_src)
check("安装包：声明了 AppMutex", _m is not None)
check("安装包：AppMutex 名字与 main.py 建的互斥体一致（%s）"
      % (_m.group(1) if _m else "?"),
      _m is not None and _n is not None and _m.group(1) == _n.group(1))
check("程序侧：启动时真的建了具名互斥体（文件锁安装程序看不见）",
      "_create_app_mutex()" in _main_src and "CreateMutexW" in _main_src)

# ---------- 批处理 / 安装脚本的换行符 ----------
# cmd 读纯 LF 的 .bat 会在某些位置把行切错：v0.9.39 打包时 build.bat 被写成了 LF，
# 日志全是 'I_ONEDIR' / 'orlevel' 不是内部或外部命令，一条被切断的 echo 还真的执行了，
# 在项目根目录建出一个字面上叫 %LOCALAPPDATA% 的文件夹。
for _bf in ("build.bat", "打包.bat", "oi桌宠.iss"):
    _bb = open(os.path.join(HERE, _bf), "rb").read()
    _crlf = _bb.count(b"\r\n")
    _lf = _bb.count(b"\n") - _crlf
    check("换行符：%s 是 CRLF（纯 LF 的批处理 cmd 会读错）" % _bf,
          _crlf > 0 and _lf == 0, "CRLF %d / 纯LF %d" % (_crlf, _lf))
_ga = os.path.join(HERE, ".gitattributes")
_gat = open(_ga, encoding="utf-8").read() if os.path.exists(_ga) else ""
check("换行符：.gitattributes 让 .bat 在任何人的电脑上都检出成 CRLF"
      "（不靠各人的 core.autocrlf）",
      "*.bat text eol=crlf" in _gat)
check("程序侧：退出时关掉互斥体句柄（不然残留会误判为仍在运行）",
      "CloseHandle(_install_mutex)" in _main_src)

print("沙箱已删除:", not os.path.exists(SAND))

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
