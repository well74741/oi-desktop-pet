# -*- coding: utf-8 -*-
"""在线更新的界面：更新弹窗（检查 → 说明 → 下载进度 → 安装）+ 后台定时检查。

逻辑都在 updater.py；这里只管界面和线程。所有网络操作都在后台线程里做，
结果用信号送回主线程（跨线程信号是队列投递，安全），界面不会卡。
"""
import os
import sys
import threading
import webbrowser

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                             QVBoxLayout)

import updater as U
from widgets import kit

_DIALOG = [None]          # 同一时间只开一个更新窗口


def _app_version():
    try:
        from module_core import APP_VERSION
        return str(APP_VERSION)
    except Exception:
        return "0"


def _mb(n):
    return "%.1f MB" % (max(0, n) / 1048576.0)


class _Bridge(QObject):
    checked = pyqtSignal(object, str)              # (info 或 None, 错误文案)
    progressed = pyqtSignal(int, int, float)       # (已下载, 总数, 字节/秒)
    downloaded = pyqtSignal(str, bool, str)        # (路径, 是否校验过 sha256, 错误)
    launched = pyqtSignal(str)                     # 错误文案（空 = 成功）


class UpdateDialog(kit.DarkDialog):
    """检查更新 / 下载 / 安装 都在这一个窗口里完成。"""

    def __init__(self, parent=None, pet=None, info=None):
        super().__init__("检查更新", parent)
        try:
            import pet_gravity
            pet_gravity._apply_dark_style(self)   # 按钮和设置窗一致
        except Exception:
            pass
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._pet = pet
        self._info = info
        self._cancel = None
        self._busy = False
        self._b = _Bridge(self)
        self._b.checked.connect(self._on_checked)
        self._b.progressed.connect(self._on_progress)
        self._b.downloaded.connect(self._on_downloaded)
        self._b.launched.connect(self._on_launched)

        lay = QVBoxLayout(self.body)
        # 这里写的都是逻辑尺寸：窗口显示时界面缩放（kit.install_ui_zoom）会统一放大。
        # 以前每个数又先乘了一遍 kit.ui()，等于放大两次 —— 380 宽的窗口实际 855。
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            "color:#dfe6f2;font-size:12px;font-family:%s;" % kit._FONT)
        lay.addWidget(self.status)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMaximumHeight(170)
        self.notes.setStyleSheet(
            "QTextBrowser{background:#161b25;color:#c7d0e0;border:1px solid #39414f;"
            "border-radius:6px;padding:4px;font-size:11px;font-family:%s;}" % kit._FONT)
        self.notes.hide()
        lay.addWidget(self.notes)
        self.bar = kit.progress(0, 100, height=8)
        self.bar.setFixedHeight(6)        # kit.progress 按气泡档位算的，这里是窗口
        self.bar.setStyleSheet(
            "QProgressBar{background:rgba(255,255,255,25);border:none;border-radius:3px;}"
            "QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #4a90e2,stop:1 #2fb8c0);border-radius:3px;}")
        self.bar.hide()
        lay.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setStyleSheet(
            "color:#8a95a8;font-size:11px;font-family:%s;" % kit._FONT)
        self.detail.hide()
        lay.addWidget(self.detail)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.auto_cb = QCheckBox("自动检查更新")
        self.auto_cb.setToolTip("启动后和每隔 6 小时自动检查一次，有新版本才提醒")
        self.auto_cb.setStyleSheet(
            "QCheckBox{color:#aab3c5;font-size:11px;font-family:%s;}" % kit._FONT)
        self.auto_cb.setChecked(bool(self._settings().get("update_auto", True)))
        self.auto_cb.toggled.connect(self._on_auto_toggled)
        row.addWidget(self.auto_cb)
        row.addStretch(1)
        self._btns = []
        self._btn_row = row
        lay.addLayout(row)
        self.setFixedWidth(340)

        if info is None:
            self.check()
        else:
            self._show_available(info)

    def _z(self, v):
        """显示之后才设的尺寸：界面缩放已经做过，要自己换算。"""
        return kit.ui_i(v) if self.property("oiZ") else v

    # ---------- 设置读写 ----------
    def _settings(self):
        if self._pet is not None and isinstance(getattr(self._pet, "settings", None), dict):
            return self._pet.settings
        try:
            import pet_gravity
            return pet_gravity.load_settings()
        except Exception:
            return {}

    def _save(self, **kv):
        st = self._settings()
        st.update(kv)
        try:
            import pet_gravity
            pet_gravity.save_settings(st)
        except Exception:
            pass

    def _on_auto_toggled(self, on):
        self._save(update_auto=bool(on))

    # ---------- 按钮 ----------
    def _set_buttons(self, *specs):
        """specs: (文字, 回调, 是否主按钮)。整排重建，状态切换时用。

        旧按钮要**当场**藏起来、摘下来：只调 deleteLater 的话，它得等事件循环
        空下来才真删，这之前旧按钮还挂在窗口上照样显示 —— 渲染出来就是新旧两排
        按钮叠在一起（实测截图看到的）。
        """
        for b in self._btns:
            self._btn_row.removeWidget(b)
            b.hide()
            b.setParent(None)
            b.deleteLater()
        self._btns = []
        for text, slot, primary in specs:
            # 和设置窗同一套按钮（kit.btn 是气泡里的按钮，按气泡档位算尺寸，
            # 放进窗口再被界面缩放放大一次，字比别处大一圈）
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            self._btn_row.addWidget(b)
            self._btns.append(b)

    def _set_status(self, text, detail=None, bar=None):
        self.status.setText(text)
        self.detail.setVisible(detail is not None)
        if detail is not None:
            self.detail.setText(detail)
        self.bar.setVisible(bar is not None)
        if bar is not None:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(bar * 1000))
        self.adjustSize()

    # ---------- 检查 ----------
    def check(self):
        self.notes.hide()
        self._set_status("正在检查新版本…（当前 v%s）" % _app_version())
        self._set_buttons(("关闭", self.reject, False))

        def work():
            try:
                self._b.checked.emit(U.fetch_latest(), "")
            except U.UpdateError as e:
                self._b.checked.emit(None, str(e))
            except Exception as e:                  # 兜底：任何意外都要回到界面
                self._b.checked.emit(None, "检查失败：%s" % e)

        threading.Thread(target=work, daemon=True, name="oi-update-check").start()

    def _on_checked(self, info, err):
        if err:
            self._show_error(err)
            return
        self._info = info
        if not U.is_newer(info["version"], _app_version()):
            self._set_status("已经是最新版本 v%s" % _app_version())
            self._set_buttons(("好的", self.accept, True))
            return
        self._show_available(info)

    def _show_available(self, info):
        cur = _app_version()
        title = "发现新版本 v%s（当前 v%s）" % (info["version"], cur)
        if info.get("title") and info["title"] != info["tag"]:
            title += "\n" + info["title"]
        notes = (info.get("notes") or "").strip()
        if notes:
            self._set_notes(notes)
        else:
            self.notes.hide()
        mode = U.install_mode()
        asset = info.get("asset")
        if mode != "installed" or not asset:
            why = {"source": "现在是从源码运行的，请用 git pull 更新。",
                   "portable": "现在用的是便携版，请到下载页下载新版。"}.get(
                mode, "这个版本没有附带安装包。")
            self._set_status(title + "\n" + why)
            self._set_buttons(("打开下载页", self._open_page, True),
                              ("以后再说", self.reject, False))
            return
        size = "（%s）" % _mb(asset["size"]) if asset.get("size") else ""
        self._set_status(title + "\n安装包 %s%s" % (asset["name"], size))
        self._set_buttons(("立即更新", self._start_download, True),
                          ("跳过此版本", self._skip, False),
                          ("以后再说", self.reject, False))

    def _set_notes(self, md):
        """显示更新说明：高度贴合内容（最多 170），标题别比正文大一圈。

        Qt 的 Markdown 标题用的是相对字号，默认 h2 比正文大很多，三行说明配一个
        大标题看着很突兀；框也不该固定撑满 170 —— 只有三行时下面全是空的。
        """
        from PyQt5.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat
        self.notes.setMarkdown(md)
        doc = self.notes.document()
        b = doc.begin()
        while b.isValid():
            if b.blockFormat().headingLevel():
                cur = QTextCursor(b)
                cur.select(QTextCursor.BlockUnderCursor)
                fmt = QTextCharFormat()
                # 用像素字号和正文（11px）对齐，只大 1px + 加粗，够区分就行
                fmt.setProperty(QTextFormat.FontPixelSize, 12)
                # Markdown 标题还带着「字号档位 +N」，不清掉的话上面的像素字号会再被放大一倍
                fmt.setProperty(QTextFormat.FontSizeAdjustment, 0)
                fmt.setFontWeight(QFont.Bold)
                cur.mergeCharFormat(fmt)
            b = b.next()
        self.notes.show()
        doc.setTextWidth(max(1, self.notes.viewport().width() or self._z(310)))
        h = int(doc.size().height()) + self._z(14)
        self.notes.setFixedHeight(max(self._z(48), min(self._z(170), h)))

    def _skip(self):
        if self._info:
            self._save(update_skip=self._info["version"])
        self.reject()

    def _open_page(self):
        try:
            webbrowser.open((self._info or {}).get("page") or U.PAGE_LATEST)
        except Exception:
            pass

    # ---------- 下载 ----------
    def _start_download(self):
        asset = (self._info or {}).get("asset") or {}
        self._busy = True
        self._cancel = threading.Event()
        self.notes.hide()          # 开始下载后说明就不用看了，窗口收紧
        self._set_status("正在下载 v%s…" % self._info["version"],
                         detail="准备中…", bar=0.0)
        self._set_buttons(("取消", self._cancel_download, False))
        cancel = self._cancel

        def work():
            try:
                U.clean_downloads()
                path, hashed = U.download(
                    asset, cancel=cancel,
                    progress=lambda d, t, s: self._b.progressed.emit(d, t, s))
                self._b.downloaded.emit(path, hashed, "")
            except U.UpdateError as e:
                self._b.downloaded.emit("", False, str(e))
            except Exception as e:
                self._b.downloaded.emit("", False, "下载失败：%s" % e)

        threading.Thread(target=work, daemon=True, name="oi-update-dl").start()

    def _on_progress(self, done, total, speed):
        frac = (done / float(total)) if total else 0.0
        txt = "%s / %s · %s/s" % (_mb(done), _mb(total) if total else "?", _mb(speed))
        self.detail.setText(txt)
        self.bar.setValue(int(frac * 1000))

    def _cancel_download(self):
        if self._cancel is not None:
            self._cancel.set()

    def _on_downloaded(self, path, hashed, err):
        self._busy = False
        if err:
            if isinstance(err, str) and err.startswith("已取消"):
                self._set_status("已取消下载。")
                self._set_buttons(("重新下载", self._start_download, True),
                                  ("关闭", self.reject, False))
                return
            self._show_error(err)
            return
        self._path = path
        ok = "已通过 sha256 校验" if hashed else "发布页没有提供校验值，仅核对了文件大小"
        self._set_status(
            "下载完成（%s）。\n点「安装并重启」后会弹出管理员授权，桌宠会自动关闭，"
            "装好后自动重新打开。" % ok, bar=1.0)
        self._set_buttons(("安装并重启", self._install, True),
                          ("以后再说", self.reject, False))

    # ---------- 安装 ----------
    def _install(self):
        self._busy = True
        self._set_status("正在启动安装程序…请在弹出的授权窗口里点「是」。", bar=1.0)
        self._set_buttons()
        path = self._path

        def work():
            try:
                U.launch_installer(path)
                self._b.launched.emit("")
            except U.UpdateError as e:
                self._b.launched.emit(str(e))
            except Exception as e:
                self._b.launched.emit("启动安装程序失败：%s" % e)

        threading.Thread(target=work, daemon=True, name="oi-update-run").start()

    def _on_launched(self, err):
        self._busy = False
        if err:
            self._set_status(err, bar=1.0)
            self._set_buttons(("再试一次", self._install, True),
                              ("关闭", self.reject, False))
            return
        self._set_status("安装程序已启动，桌宠即将关闭……")
        # 让出一点时间给安装程序起来，再退出，好让它替换文件
        QTimer.singleShot(600, self._quit_app)

    def _quit_app(self):
        q = getattr(self._pet, "quit_requested", None) if self._pet else None
        try:
            if callable(q):
                q()
                return
        except Exception:
            pass
        from PyQt5.QtWidgets import QApplication
        QApplication.quit()

    # ---------- 出错 ----------
    def _show_error(self, err):
        self._set_status(err)
        self._set_buttons(("重试", self.check if not self._info else self._retry, True),
                          ("打开下载页", self._open_page, False),
                          ("关闭", self.reject, False))

    def _retry(self):
        if self._info and self._info.get("asset") and U.install_mode() == "installed":
            self._start_download()
        else:
            self.check()

    # ---------- 关闭 ----------
    def reject(self):
        if self._cancel is not None:
            self._cancel.set()        # 关窗口就取消下载，不在后台偷偷下完
        super().reject()

    def done(self, r):
        _DIALOG[0] = None
        super().done(r)


def open_dialog(parent=None, pet=None, info=None, anchor=None):
    """打开更新窗口（已经开着就提到前面，不开第二个）。"""
    d = _DIALOG[0]
    if d is not None:
        try:
            d.raise_()
            d.activateWindow()
            return d
        except RuntimeError:
            _DIALOG[0] = None
    d = UpdateDialog(parent, pet, info)
    _DIALOG[0] = d
    d.adjustSize()
    kit.place_near(d, anchor if anchor is not None else pet)
    d.show()
    d.raise_()
    d.activateWindow()
    return d


class UpdateScheduler(QObject):
    """后台自动检查：启动后 FIRST_DELAY 检查一次，之后每 INTERVAL 一次。

    - 关掉「自动检查更新」就什么都不做；
    - 用户点过「跳过此版本」的那个版本不再提醒；
    - 同一个版本这次运行只提醒一次（点了「以后再说」就别再追着弹）；
    - 正在全屏（看视频、打游戏）时先憋着，退出全屏再弹。
    检查失败一律安静：自动检查不该因为没网而弹错误窗。
    """

    FIRST_DELAY_MS = 30 * 1000
    INTERVAL_MS = 6 * 3600 * 1000
    FULLSCREEN_RETRY_MS = 60 * 1000

    _found = pyqtSignal(object)

    def __init__(self, pet):
        super().__init__(pet)
        self._pet = pet
        self._prompted = set()
        self._pending = None
        self._running = False
        self._found.connect(self._on_found)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)

    def start(self):
        QTimer.singleShot(self.FIRST_DELAY_MS, self.check_now)
        self._timer.start()

    def _settings(self):
        st = getattr(self._pet, "settings", None)
        return st if isinstance(st, dict) else {}

    def check_now(self):
        if self._running or not self._settings().get("update_auto", True):
            return
        if U.install_mode() == "source":
            return            # 源码运行时不打扰（开发中自己 git pull）
        self._running = True

        def work():
            try:
                info = U.fetch_latest()
            except Exception:
                info = None
            self._found.emit(info)

        threading.Thread(target=work, daemon=True, name="oi-update-auto").start()

    def _on_found(self, info):
        self._running = False
        if not info or not U.is_newer(info["version"], _app_version()):
            return
        v = info["version"]
        if v == str(self._settings().get("update_skip", "") or "") or v in self._prompted:
            return
        self._pending = info
        self._show_when_ok()

    def _show_when_ok(self):
        info = self._pending
        if info is None:
            return
        if getattr(self._pet, "is_fullscreen", False):
            QTimer.singleShot(self.FULLSCREEN_RETRY_MS, self._show_when_ok)
            return
        self._pending = None
        self._prompted.add(info["version"])
        open_dialog(None, self._pet, info=info, anchor=self._pet)


def start_scheduler(pet):
    """main.py 在桌宠显示后调用。返回调度器（挂在 pet 上，跟着它的生命周期）。"""
    s = UpdateScheduler(pet)
    s.start()
    pet._update_scheduler = s
    return s
