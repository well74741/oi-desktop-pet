# -*- coding: utf-8 -*-
"""在线更新自动化测试：版本比较 / 挑附件 / 查询 / 下载校验 / 取消 / 弹窗状态 / 自动检查。

运行：python test_update.py（离屏；用本地假服务器扮演 GitHub，**不联网**；
**不会真的启动安装程序**（launch_installer 打桩）；用户数据进临时沙箱）。

真正测不了、需要人工确认的只有一步：安装程序弹 UAC → 静默安装 → 以普通用户身份
重新打开桌宠。这一步要两个都带在线更新的正式版本（先装 A，再发布 B）才能走通。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data_store                                    # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_update_test_")
data_store.DATA_DIR = SAND
import pet_gravity                                   # noqa: E402

_cfg = pet_gravity.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

from PyQt5.QtWidgets import QApplication, QWidget    # noqa: E402

app = QApplication([])
import updater as U                                  # noqa: E402
import update_ui as UI                               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def pump(cond, sec=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------- 假 GitHub ----------
PAYLOAD = os.urandom(300 * 1024 + 123)          # 不是整块大小，顺便测尾巴
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SRV = {"api": None, "api_code": 200, "file_mode": "ok", "file_delay": 0.0}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api"):
            if SRV["api_code"] != 200:
                self.send_response(SRV["api_code"])
                self.end_headers()
                return
            body = SRV["api"] if isinstance(SRV["api"], bytes) else \
                json.dumps(SRV["api"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/file"):
            data = PAYLOAD
            if SRV["file_mode"] == "truncate":
                data = PAYLOAD[: len(PAYLOAD) // 2]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                for i in range(0, len(data), 16 * 1024):
                    self.wfile.write(data[i:i + 16 * 1024])
                    self.wfile.flush()
                    if SRV["file_delay"]:
                        time.sleep(SRV["file_delay"])
            except Exception:
                pass
            return
        self.send_response(404)
        self.end_headers()


srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]
API = BASE + "/api"


def release(tag="0.9.99", digest="sha256:" + SHA, size=None, name="oi._Setup_v0.9.99.exe"):
    return {"tag_name": tag, "name": "测试发布", "body": "## 更新内容\n- 修了个 bug",
            "html_url": BASE + "/page",
            "assets": [
                {"name": "oi.0.9.99.exe", "size": 1, "state": "uploaded",
                 "browser_download_url": BASE + "/portable"},
                {"name": name, "size": len(PAYLOAD) if size is None else size,
                 "state": "uploaded", "digest": digest,
                 "browser_download_url": BASE + "/file"}]}


DL = tempfile.mkdtemp(prefix="oi_update_dl_")
try:
    # ===== 一、版本比较 =====
    print("--- 一、版本号 ---")
    cases = [("0.9.40", "0.9.39", True), ("0.9.39", "0.9.39", False),
             ("0.9.38", "0.9.39", False), ("0.10.0", "0.9.99", True),
             ("v1.0", "0.9.99", True), ("1.0", "1.0.0", False),
             ("0.9.39.1", "0.9.39", True), ("nightly", "0.9.39", False)]
    bad = [c for c in cases if U.is_newer(c[0], c[1]) != c[2]]
    check("版本按数字逐段比较（0.10.0 > 0.9.99，1.0 == 1.0.0，认不出的不算新）",
          not bad, "错的: %s" % bad)
    check("带不带 v 前缀都认（GitHub 上这次的标签就没写 v）",
          U.parse_version("v0.9.37") == U.parse_version("0.9.37") == (0, 9, 37))

    # ===== 二、挑附件 =====
    print("\n--- 二、挑附件 ---")
    a = U.pick_asset(release()["assets"])
    check("被 GitHub 改了名的中文安装包也能挑中（只认 setup + .exe）",
          a is not None and a["name"] == "oi._Setup_v0.9.99.exe")
    check("便携版 exe 不会被当成安装包",
          U.pick_asset([{"name": "oi.0.9.99.exe", "state": "uploaded"}]) is None)
    check("还没传完的附件（state 不是 uploaded）不选",
          U.pick_asset([{"name": "x_Setup.exe", "state": "starter"}]) is None)
    check("校验值只认合法的 sha256",
          U._sha_of({"digest": "sha256:" + SHA}) == SHA
          and U._sha_of({"digest": None}) is None
          and U._sha_of({"digest": "sha256:zz"}) is None)

    # ===== 三、查询 =====
    print("\n--- 三、查询最新版本 ---")
    SRV["api"] = release()
    info = U.fetch_latest(API)
    check("正常查询：版本 / 附件 / 大小 / sha256 都取到",
          info["version"] == "0.9.99" and info["asset"]["sha256"] == SHA
          and info["asset"]["size"] == len(PAYLOAD) and info["notes"])
    for code, word in ((404, "还没有发布"), (403, "太频繁"), (500, "HTTP 500")):
        SRV["api_code"] = code
        try:
            U.fetch_latest(API)
            msg = "（没抛错）"
        except U.UpdateError as e:
            msg = str(e)
        check("HTTP %d → 给用户看得懂的提示" % code, word in msg, msg)
    SRV["api_code"] = 200
    SRV["api"] = b"<html>not json"
    try:
        U.fetch_latest(API)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("返回的不是 JSON → 提示无法解析，不崩", "无法解析" in msg, msg)
    SRV["api"] = release(tag="latest-build")
    try:
        U.fetch_latest(API)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("标签不是版本号 → 明确报出来", "不是版本号" in msg, msg)
    try:
        U.fetch_latest("http://127.0.0.1:1/api", timeout=2)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("连不上 → 提示检查网络", "网络" in msg, msg)

    # ===== 四、下载 =====
    print("\n--- 四、下载与校验 ---")
    SRV["api"] = release()
    asset = U.fetch_latest(API)["asset"]
    seen = []
    path, hashed = U.download(asset, dest_dir=DL,
                              progress=lambda d, t, s: seen.append((d, t)))
    ok_bytes = open(path, "rb").read() == PAYLOAD
    check("下载成功：内容一字节不差，并且做了 sha256 校验", ok_bytes and hashed)
    check("进度回调单调增加，最后一次 = 总大小",
          bool(seen) and all(seen[i][0] <= seen[i + 1][0] for i in range(len(seen) - 1))
          and seen[-1][0] == seen[-1][1] == len(PAYLOAD), "回调 %d 次" % len(seen))
    check("落盘文件名只含安全字符（中文/怪字符已替换）",
          all(c.isalnum() or c in "._-" for c in os.path.basename(path)),
          os.path.basename(path))
    check("没有残留 .part 半截文件", not any(n.endswith(".part") for n in os.listdir(DL)))

    def leftovers():
        return [n for n in os.listdir(DL)]

    shutil.rmtree(DL)
    os.makedirs(DL)
    bad_asset = dict(asset, sha256="0" * 64)
    try:
        U.download(bad_asset, dest_dir=DL)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("sha256 对不上 → 拒绝并删除，不留任何文件", "校验失败" in msg and not leftovers(),
          "%s / 残留 %s" % (msg, leftovers()))

    SRV["file_mode"] = "truncate"
    try:
        U.download(asset, dest_dir=DL)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    SRV["file_mode"] = "ok"
    check("下载不完整（只收到一半）→ 报错且不留文件",
          "不完整" in msg and not leftovers(), "%s / 残留 %s" % (msg, leftovers()))

    SRV["file_delay"] = 0.02
    cancel = threading.Event()
    got = []

    def _prog(d, t, s):
        got.append(d)
        if d > 64 * 1024:
            cancel.set()
    try:
        U.download(asset, dest_dir=DL, cancel=cancel, progress=_prog)
        msg = "（没抛错）"
    except U.UpdateCancelled as e:
        msg = str(e)
    except U.UpdateError as e:
        msg = "不是取消而是：" + str(e)
    SRV["file_delay"] = 0.0
    check("下载中途取消 → 立即停止，半截文件删掉",
          "已取消" in msg and not leftovers() and max(got) < len(PAYLOAD),
          "%s / 停在 %d 字节" % (msg, max(got) if got else 0))

    path, hashed = U.download(dict(asset, sha256=None), dest_dir=DL)
    check("发布页没给校验值时：只核对大小也能下完，并如实标记没做 sha256",
          os.path.isfile(path) and hashed is False)

    # ===== 五、启动安装程序（打桩，不真的运行）=====
    print("\n--- 五、启动安装程序 ---")
    calls = []
    _real_startfile = getattr(os, "startfile", None)
    os.startfile = lambda *a: calls.append(a)
    U.launch_installer(path)
    c = calls[-1] if calls else ()
    check("用 ShellExecute（os.startfile）启动 —— 这样才会弹 UAC", bool(calls))
    check("静默参数带 /NOCLOSEAPPLICATIONS（不偷偷关别的程序）、不带 /SUPPRESSMSGBOXES",
          len(c) > 2 and "/SILENT" in c[2] and "/NOCLOSEAPPLICATIONS" in c[2]
          and "/SUPPRESSMSGBOXES" not in c[2], str(c[2:3]))
    check("工作目录是下载目录，不是桌宠安装目录（v0.9.31 的 DLL 占用教训）",
          len(c) > 3 and os.path.normcase(c[3]) == os.path.normcase(os.path.dirname(path)))

    def _deny(*a):
        e = OSError("cancelled")
        e.winerror = 1223
        raise e
    os.startfile = _deny
    try:
        U.launch_installer(path)
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("用户在 UAC 上点「否」→ 提示取消了授权（不会退出桌宠）", "取消了管理员授权" in msg, msg)
    try:
        U.launch_installer(os.path.join(DL, "不存在.exe"))
        msg = "（没抛错）"
    except U.UpdateError as e:
        msg = str(e)
    check("安装包被删了 → 提示重新下载", "重新下载" in msg, msg)
    if _real_startfile is not None:
        os.startfile = _real_startfile

    # ===== 六、更新弹窗 =====
    print("\n--- 六、更新弹窗 ---")
    _real_fetch, _real_dl, _real_launch = U.fetch_latest, U.download, U.launch_installer
    _real_mode = U.install_mode
    U.fetch_latest = lambda *a, **k: _real_fetch(API)
    U.download = lambda asset, **k: _real_dl(asset, dest_dir=DL, **k)

    class Pet(QWidget):
        def __init__(self):
            super().__init__()
            self.settings = pet_gravity.load_settings()
            self.is_fullscreen = False
            self.quit_calls = 0

        def quit_requested(self):
            self.quit_calls += 1

    pet = Pet()

    def btn_texts(d):
        return [b.text() for b in d._btns]

    UI._app_version = lambda: "0.9.99"
    d = UI.open_dialog(None, pet)
    pump(lambda: btn_texts(d) == ["好的"])
    check("已是最新 → 显示「已经是最新版本」+ 一个「好的」",
          "最新版本" in d.status.text() and btn_texts(d) == ["好的"], d.status.text())
    d.accept()
    check("关掉之后可以再开（不会卡住单例）", UI._DIALOG[0] is None)

    UI._app_version = lambda: "0.9.39"
    U.install_mode = lambda: "source"
    d = UI.open_dialog(None, pet)
    pump(lambda: "发现新版本" in d.status.text())
    check("源码运行发现新版 → 只给「打开下载页」，不尝试自动安装",
          btn_texts(d) == ["打开下载页", "以后再说"] and "git pull" in d.status.text(),
          str(btn_texts(d)))
    check("有更新说明时把它显示出来", d.notes.isVisible() or not d.notes.isHidden())
    d.reject()

    U.install_mode = lambda: "installed"
    d = UI.open_dialog(None, pet)
    pump(lambda: "发现新版本" in d.status.text())
    check("安装版发现新版 → 立即更新 / 跳过此版本 / 以后再说",
          btn_texts(d) == ["立即更新", "跳过此版本", "以后再说"], str(btn_texts(d)))
    d._btns[1].click()
    check("「跳过此版本」记进设置", pet.settings.get("update_skip") == "0.9.99"
          and pet_gravity.load_settings().get("update_skip") == "0.9.99")

    launched = []
    U.launch_installer = lambda p: launched.append(p)
    d = UI.open_dialog(None, pet)
    pump(lambda: btn_texts(d) and btn_texts(d)[0] == "立即更新")
    SRV["file_delay"] = 0.01
    vals = []
    d.bar.valueChanged.connect(vals.append)
    d._btns[0].click()
    check("开始下载后显示进度条 + 「取消」", d.bar.isVisible() and btn_texts(d) == ["取消"])
    # 回归：换状态时旧按钮必须当场消失。只 deleteLater 的话旧按钮还挂在窗口上
    # 照样显示，渲染出来新旧两排按钮叠在一起（实测截图看到过）
    from PyQt5.QtWidgets import QPushButton as _QPB
    # 往已显示的窗口里加按钮时，Qt 把"显示"排到下一轮事件循环（布局里的
    # _q_showIfNotHidden 是队列调用），所以先处理一轮事件再看
    pump(lambda: any(b.isVisible() for b in d._btns), 2)
    _vis = [b.text() for b in d.findChildren(_QPB) if b.isVisible()]
    check("换状态后窗口上只剩新按钮（旧的那排当场消失，不叠在一起）",
          _vis == ["取消"], "可见按钮=%s" % _vis)
    pump(lambda: btn_texts(d) and btn_texts(d)[0] == "安装并重启", 10)
    SRV["file_delay"] = 0.0
    check("下载完 → 提示已校验 + 「安装并重启」",
          "sha256" in d.status.text() and btn_texts(d)[0] == "安装并重启", d.status.text())
    check("进度条一路走到底（中间有多个过渡值）",
          len(set(vals)) >= 4 and max(vals) == 1000, "%d 个不同值" % len(set(vals)))
    d._btns[0].click()
    pump(lambda: pet.quit_calls > 0, 5)
    check("安装程序启动后桌宠走正常退出流程（好让安装程序替换文件）",
          launched and pet.quit_calls == 1, "launched=%s quit=%d" % (bool(launched), pet.quit_calls))
    try:
        d.close()
    except RuntimeError:
        pass
    UI._DIALOG[0] = None

    # 关窗口要取消下载
    SRV["file_delay"] = 0.03
    d = UI.open_dialog(None, pet)
    pump(lambda: btn_texts(d) and btn_texts(d)[0] == "立即更新")
    d._btns[0].click()
    pump(lambda: d.bar.value() > 0, 5)
    ev = d._cancel
    d.reject()
    check("下载中关掉窗口 → 下载随之取消（不在后台偷偷下完）", ev is not None and ev.is_set())
    SRV["file_delay"] = 0.0
    time.sleep(0.3)

    # ===== 七、自动检查 =====
    print("\n--- 七、自动检查 ---")
    pet.settings["update_skip"] = ""
    sch = UI.UpdateScheduler(pet)
    opened = []
    _real_open = UI.open_dialog
    UI.open_dialog = lambda *a, **k: opened.append(k.get("info"))
    sch.check_now()
    pump(lambda: opened, 5)
    check("自动检查发现新版 → 弹更新窗（带着查到的信息，不再查一遍）",
          len(opened) == 1 and opened[0]["version"] == "0.9.99")
    sch.check_now()
    pump(lambda: not sch._running, 5)
    check("同一个版本这次运行只提醒一次（点了「以后再说」就不追着弹）", len(opened) == 1)

    sch2 = UI.UpdateScheduler(pet)
    pet.settings["update_skip"] = "0.9.99"
    sch2.check_now()
    pump(lambda: not sch2._running, 5)
    check("被「跳过」的版本不提醒", len(opened) == 1)

    sch3 = UI.UpdateScheduler(pet)
    sch3.FULLSCREEN_RETRY_MS = 100
    pet.settings["update_skip"] = ""
    pet.is_fullscreen = True
    sch3.check_now()
    pump(lambda: not sch3._running, 5)
    pump(lambda: False, 0.4)
    check("正在全屏时先不弹", len(opened) == 1)
    pet.is_fullscreen = False
    pump(lambda: len(opened) == 2, 3)
    check("退出全屏后再弹出来", len(opened) == 2)

    sch4 = UI.UpdateScheduler(pet)
    pet.settings["update_auto"] = False
    sch4.check_now()
    check("关掉「自动检查更新」后完全不查", not sch4._running)
    pet.settings["update_auto"] = True

    U.install_mode = lambda: "source"
    sch5 = UI.UpdateScheduler(pet)
    sch5.check_now()
    check("源码运行时自动检查不打扰（开发者自己 git pull）", not sch5._running)
    UI.open_dialog = _real_open

    # ===== 八、静态检查：安装包与打包配置 =====
    print("\n--- 八、安装包配置 ---")
    iss = open(os.path.join(HERE, "oi桌宠.iss"), encoding="utf-8").read()
    check("静默更新后会重新打开桌宠（skipifnotsilent）",
          "skipifnotsilent" in iss)
    check("重新打开时用普通用户身份（runasoriginaluser），不让数据存进安装目录",
          any("skipifnotsilent" in l and "runasoriginaluser" in l for l in iss.splitlines()))
    import re as _re
    m = _re.search(r"OutputBaseFilename=(\S+)", iss)
    check("安装包文件名是纯英文（GitHub 会改掉中文名）",
          bool(m) and all(ord(ch) < 128 for ch in m.group(1)), m.group(1) if m else "")
    spec = open(os.path.join(HERE, "oi_pet_v020.spec"), encoding="utf-8").read()
    check("打包清单带上了 updater / update_ui", "'updater'" in spec and "'update_ui'" in spec)
    st = pet_gravity.load_settings()
    check("设置默认：自动检查开启、没有跳过的版本",
          "update_auto" in st and "update_skip" in st)
finally:
    srv.shutdown()
    shutil.rmtree(DL, ignore_errors=True)
    shutil.rmtree(SAND, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
