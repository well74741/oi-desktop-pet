# -*- coding: utf-8 -*-
"""聚合AI 的界面层：左侧边栏 + 站点管理窗。

分层：
- `webchat_launcher.py` 是纯后端（只用 Win32 / subprocess，不碰 Qt），于是
  "启动浏览器 + 等窗口出现"这一秒多可以丢到后台线程，不卡桌宠动画；
- 本模块只做界面。两边互不拖累。

为什么放顶层而不是放进 widgets/：widgets/*.py 是运行期按文件路径动态加载的，
PyInstaller 静态分析看不见它们的依赖，漏打包过一次（v0.9.2 画布/拼豆加载
失败就是这个）。顶层模块被 bubble_layout 静态 import，打包工具能正常收集。

交互约定：气泡上的「打开」就是打开（开上次那个站点，不弹列表）。**换模型、
加站点、置顶全部在聚合AI 窗口左边那条侧边栏上**——手已经在浏览器上了，不用
再回桌宠点一遍。切站点复用同一个窗口（关掉上一个），像换标签页。

窗口结构（这一版的关键）：桌宠开一个自己的**宿主窗口** `WebChatHost`，里面
用 Qt 布局排「侧边栏 | 网页容器」，再用 `SetParent` 把浏览器的 `--app` 窗口
塞进那个容器。于是侧边栏占的是**真实布局空间**，网页被挤窄而不是被盖住。
反过来做（侧边栏当浏览器的子窗口）永远只能浮在网页上方，因为浏览器不给
第三方留内容区——这是上一版被否掉的原因。
"""

import threading

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer, QObject, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from widgets import kit

# ==================== 定位辅助 ====================

def pet_rect_of(widget):
    """从控件往上找桌宠，取它的全局矩形（用来把聚合AI 窗口摆在旁边）。"""
    w = widget
    while w is not None:
        pet = getattr(w, "pet", None)
        if pet is not None:
            try:
                g = pet.frameGeometry()
                return (g.x(), g.y(), g.width(), g.height())
            except Exception:
                return None
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
    return None


def work_area_of(near):
    scr = None
    if near:
        scr = QApplication.screenAt(QPoint(near[0] + near[2] // 2,
                                           near[1] + near[3] // 2))
    scr = scr or QApplication.primaryScreen()
    a = scr.availableGeometry()
    return (a.x(), a.y(), a.width(), a.height())


# ==================== 后台打开 ====================

class _Opener(QObject):
    """后台线程打开网页窗口，完成后回主线程把它嵌进宿主。

    启动浏览器 + 等窗口出现要一秒多，放在主线程会让桌宠动画卡住。
    """

    done = pyqtSignal(bool, str, object)     # (成功?, 错误信息, 完成回调)

    def __init__(self):
        super().__init__()
        self.done.connect(self._on_done)
        self._busy = False

    def busy(self):
        return self._busy

    def open(self, site, size, finished=None):
        """开始打开；已经有一个在开了就返回 False（调用方据此复原按钮）。"""
        import webchat_launcher as L
        if self._busy:
            return False
        self._busy = True

        def work():
            try:
                ok, err, _h = L.open_site(site, size=size)
            except Exception as e:
                ok, err = False, str(e)
            self.done.emit(ok, err, finished)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _on_done(self, ok, err, finished):
        self._busy = False
        if not ok and not dock_mode():
            h = host(create=False)
            if h is not None:
                h.set_loading("")   # 开失败了，别让提示一直转
        if ok:
            import webchat_launcher as L
            _name, hwnd = L.active()
            if dock_mode():
                attach_dock(hwnd)          # 站点栏挂进网页窗口
            else:
                h = host()
                if hwnd and h is not None:
                    h.attach(hwnd)  # 接管新窗口，内部会把上一个摘掉并关掉
        if callable(finished):
            try:
                finished(ok, err)
            except Exception:
                pass


def L_preferred_size():
    """贴边栏模式下网页窗口该开多大：沿用上次用过的尺寸。"""
    try:
        import webchat_launcher as L
        return L.preferred_size()
    except Exception:
        return (960, 720)


_OPENER = None


def opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = _Opener()
    return _OPENER


def open_site_ui(site, near=None, finished=None):
    """打开一个站点。

    贴边栏模式下**不开宿主窗口**：浏览器窗口自己就是那个窗口，开出来之后把
    站点栏挂进去（见 _Opener._on_done）。旧架构仍走下面那条宿主路径。
    """
    if dock_mode():
        return opener().open(site, L_preferred_size(), finished)
    h = host()
    h.place_near(near)             # 只有首次（还没显示过）才摆位
    if not h.isVisible():
        h.show()
    h.raise_()
    h.activateWindow()
    # 浏览器起来要一秒多，这期间容器是纯色的——先亮一条加载提示，
    # 否则看着像打不开（用户反馈"等了很久，加载时画面是空白的"）
    h.set_loading(str((site or {}).get("name") or ""))
    return opener().open(site, h.holder_size(), finished)


# ==================== 贴边栏模式（方案一） ====================
# 旧架构：我们开宿主窗口，把浏览器窗口摆到容器上 —— 两个顶层窗口、两个进程，
# 永远不可能原子地一起移动，中间那一帧就是"断层 / 拖动延迟"。
# 贴边栏模式：浏览器窗口就是那个窗口，站点栏挂进去当它的子窗口，位置由系统
# 保证跟随（实测相对偏移恒定）。详见 webchat_dock.py。
#
# 用设置里的 `webchat_dock` 开关切换，旧路径完整保留做退路。

_DOCK = None


def dock_mode():
    """当前是不是贴边栏模式（默认开；settings 里可以关回旧架构）。"""
    try:
        import pet_gravity
        return bool(pet_gravity.load_settings().get("webchat_dock", True))
    except Exception:
        return True


def dock(create=True):
    global _DOCK
    if _DOCK is None and create:
        from webchat_dock import DockBar
        _DOCK = DockBar()
    return _DOCK


def attach_dock(hwnd, near=None):
    """把站点栏挂到网页窗口上，并把窗口本身摆好显示出来（贴边栏模式的"接管"）。

    **必须自己 restore + 摆位**：网页窗口是 `--hide` 着开出来的，旧架构里由宿主
    负责把它摆到容器上；贴边栏模式下没有宿主了，没人管它就停在 (-32000,-32000)
    那个"最小化"坐标上，窗口等于看不见（第一版就栽在这，而且断言只比了相对位置
    所以还"通过"了）。
    """
    import webchat_launcher as L
    if not hwnd:
        return False
    L.restore_browser(hwnd, near=near)
    L.show_browser(hwnd)
    L.focus_browser(hwnd)
    d = dock()
    ok = d.attach_to(hwnd)
    try:
        d.bar.refresh_active()
    except Exception:
        pass
    return ok


# ==================== 打开入口 ====================

def open_webchat(parent=None, finished=None):
    """气泡按钮 / 组件按钮的「打开」：直接开上次用的站点。

    以前这里弹一张站点列表，每次开都要再选一遍模型。现在切换模型、加站点、
    置顶全挪进聚合AI 窗口左边的侧边栏了——那时候手就在浏览器上，比回桌宠
    点菜单顺手，这个按钮只管"把聚合AI 打开"。

    返回 True 表示已经交给后台线程去开（finished 稍后会被调用）。
    """
    import webchat_launcher as L
    exe, _name = L.browser_path()
    if not exe:
        kit.warn(parent, "聚合AI",
                 "没找到 Edge 或 Chrome。\n聚合AI 用系统浏览器的应用窗口模式打开"
                 "网页版大模型（无地址栏、无标签页），请先安装其中之一。")
        return False
    sites = L.load_sites()
    if not sites:
        kit.warn(parent, "聚合AI", "站点列表是空的。")
        return False
    name, hwnd = L.active()
    site = None
    if hwnd:                       # 已经开着：就用那个（等于回到那个窗口）
        site = next((s for s in sites if str(s.get("name")) == name), None)
    if site is None:
        last = L.last_site()
        site = next((s for s in sites if str(s.get("name")) == last), None)
    if site is None:
        site = sites[0]
    return open_site_ui(site, near=pet_rect_of(parent), finished=finished)


# ==================== 站点管理 ====================

class SiteManagerDialog(kit.DarkDialog):
    """站点管理：添加 / 编辑 / 删除 / 调整顺序。"""

    def __init__(self, parent=None):
        super().__init__("聚合AI · 站点管理", parent)
        import webchat_launcher as L
        self._L = L
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        tip = QLabel("列表顺序就是菜单与侧边栏的顺序。网址填该站点的聊天页面地址。")
        tip.setStyleSheet("color:#aab3c5;font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        body = QHBoxLayout()
        body.setSpacing(8)
        self.listw = QListWidget()
        self.listw.setMinimumHeight(190)
        self.listw.currentRowChanged.connect(self._on_select)
        body.addWidget(self.listw, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        for text, slot in (("上移", lambda: self._move(-1)),
                           ("下移", lambda: self._move(1)),
                           ("删除", self._remove)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body)

        form = QHBoxLayout()
        form.setSpacing(6)
        form.addWidget(QLabel("名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：DeepSeek")
        form.addWidget(self.name_edit, 1)
        lay.addLayout(form)

        form2 = QHBoxLayout()
        form2.setSpacing(6)
        form2.addWidget(QLabel("网址"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://chat.example.com")
        form2.addWidget(self.url_edit, 1)
        lay.addLayout(form2)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        # 「添加」与「保存修改」分开：合成一个按钮时，只要列表里选中了某行就会
        # 变成覆盖那一行，看着像添加、其实把已有站点改掉了。
        add = QPushButton("添加")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add)
        btns.addWidget(add)
        upd = QPushButton("保存修改")
        upd.setCursor(Qt.PointingHandCursor)
        upd.clicked.connect(self._update)
        btns.addWidget(upd)
        btns.addStretch(1)
        ok = QPushButton("完成")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self._sites = [dict(s) for s in L.load_sites()]
        self._reload()

    def focus_new(self):
        """侧边栏 `＋` 进来时：清空表单、光标落在名称上，直接就能添加。"""
        self.listw.setCurrentRow(-1)
        self.name_edit.clear()
        self.url_edit.clear()
        self.name_edit.setFocus()
        return self

    # ---------- 列表 ----------
    def _reload(self, row=0):
        self.listw.blockSignals(True)
        self.listw.clear()
        for s in self._sites:
            QListWidgetItem("%s    %s" % (s.get("name", ""), s.get("url", "")),
                            self.listw)
        self.listw.blockSignals(False)
        if self._sites:
            self.listw.setCurrentRow(max(0, min(row, len(self._sites) - 1)))

    def _on_select(self, row):
        if 0 <= row < len(self._sites):
            self.name_edit.setText(str(self._sites[row].get("name", "")))
            self.url_edit.setText(str(self._sites[row].get("url", "")))

    def _form(self):
        """读表单；不合法时提示并返回 None。网址缺协议头自动补 https://。"""
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url:
            kit.warn(self, "站点", "名称和网址都要填。")
            return None
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return {"name": name, "url": url}

    def _add(self):
        item = self._form()
        if not item:
            return
        if any(str(s.get("name", "")) == item["name"] for s in self._sites):
            kit.warn(self, "站点", "已经有叫「%s」的站点了。\n改它的话选中后用"
                                   "「保存修改」。" % item["name"])
            return
        self._sites.append(item)
        self._save()
        self._reload(len(self._sites) - 1)

    def _update(self):
        row = self.listw.currentRow()
        if not (0 <= row < len(self._sites)):
            kit.warn(self, "站点", "先在上面选中要修改的站点。")
            return
        item = self._form()
        if not item:
            return
        dup = next((i for i, s in enumerate(self._sites)
                    if str(s.get("name", "")) == item["name"] and i != row), None)
        if dup is not None:
            kit.warn(self, "站点", "已经有叫「%s」的站点了。" % item["name"])
            return
        self._sites[row] = item
        self._save()
        self._reload(row)

    def _move(self, delta):
        row = self.listw.currentRow()
        new = row + delta
        if 0 <= row < len(self._sites) and 0 <= new < len(self._sites):
            self._sites[row], self._sites[new] = self._sites[new], self._sites[row]
            self._save()
            self._reload(new)

    def _remove(self):
        row = self.listw.currentRow()
        if 0 <= row < len(self._sites):
            self._sites.pop(row)
            self._save()
            self._reload(row)

    def _save(self):
        try:
            self._L.save_sites(self._sites)
        except Exception as e:
            kit.warn(self, "站点", "保存失败：%s" % e)
        refresh_sidebar()


# ==================== 宿主窗口 + 左侧边栏 ====================
#
# 关键决定：**不是把侧边栏挂到浏览器窗口上，而是把浏览器窗口塞进桌宠自己的
# 宿主窗口里**。宿主用 Qt 布局排"侧边栏 | 网页容器"，侧边栏因此占的是真实
# 布局空间——网页被挤窄，不会被盖住一条。反过来做（侧边栏当浏览器的子窗口）
# 永远只能浮在网页上方，因为浏览器不给第三方留内容区。
#
# 副作用都是好的：宿主是正经 Qt 顶层窗口，坐标、菜单、悬停提示全部正常；
# 网页是子窗口，移动/缩放由系统同步，零延迟；任务栏只有一个窗口。

_TILE_QSS = (
    "QPushButton{border:1px solid rgba(255,255,255,26);border-radius:6px;"
    "background:rgba(255,255,255,14);color:#cfd6e6;font-size:13px;"
    "font-weight:bold;}"
    "QPushButton:hover{background:rgba(116,164,255,60);border-color:#7db6ff;}"
    "QPushButton:pressed{background:rgba(74,144,226,120);}"
)

_ACTIVE_QSS = (
    "QPushButton{border:1px solid #7db6ff;border-radius:6px;"
    "background:rgba(74,144,226,150);color:#ffffff;font-size:13px;"
    "font-weight:bold;}"
)

_MINI_QSS = (
    "QPushButton{border:none;background:transparent;color:#9fb0cc;"
    "font-size:13px;}"
    "QPushButton:hover{color:#ffffff;background:rgba(255,255,255,22);"
    "border-radius:5px;}"
)


class WebChatSidebar(QWidget):
    """宿主左边那条常驻站点栏。

    就是个普通 Qt 子控件——它占布局空间，网页容器拿剩下的。没有定时器、没有
    Win32、没有收起：位置由布局管，换站点点方块。
    """

    PANEL_W = 42       # 栏宽（逻辑像素，未乘界面基准倍率）
    TILE = 30          # 方块按钮边长

    def __init__(self, parent=None):
        super().__init__(parent)
        import webchat_launcher as L
        self._L = L
        self.setFixedWidth(kit.ui(self.PANEL_W))
        # 尺寸已按 kit.ui() 算过，别再被全局缩放过滤器放大第二遍
        self.setProperty("oi_nozoom", True)
        self._tiles = {}
        self._active = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(kit.ui(6), kit.ui(6), kit.ui(6), kit.ui(6))
        lay.setSpacing(kit.ui(5))

        # 站点多过窗口高度时可以滚（滚动条隐藏，滚轮/拖动都能用）
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}"
                                   "QWidget{background:transparent;}")
        holder = QWidget()
        self._tiles_lay = QVBoxLayout(holder)
        self._tiles_lay.setContentsMargins(0, 0, 0, 0)
        self._tiles_lay.setSpacing(kit.ui(5))
        self._tiles_lay.addStretch(1)
        self._scroll.setWidget(holder)
        lay.addWidget(self._scroll, 1)

        self._add_btn = self._mini("＋", "添加站点", self._on_add)
        lay.addWidget(self._add_btn, 0, Qt.AlignHCenter)
        self._set_btn = self._mini("⚙", "设置", self._on_settings)
        lay.addWidget(self._set_btn, 0, Qt.AlignHCenter)

        self.refresh()

    def _mini(self, text, tip, slot):
        b = QPushButton(text, self)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(kit.ui(self.TILE), kit.ui(22))
        b.setStyleSheet(kit.ui_qss(_MINI_QSS))
        b.clicked.connect(slot)
        return b

    def paintEvent(self, ev):
        QPainter(self).fillRect(self.rect(), QColor(30, 36, 48))

    # ---------- 站点按钮 ----------
    def refresh(self):
        """按站点列表重建方块按钮（增删改站点后调）。"""
        for b in self._tiles.values():
            b.setParent(None)
            b.deleteLater()
        self._tiles = {}
        for i, s in enumerate(self._L.load_sites()):
            name = str(s.get("name") or s.get("url") or "")
            if not name:
                continue
            b = QPushButton(name[0], self)
            b.setToolTip(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(kit.ui(self.TILE), kit.ui(self.TILE))
            b.setStyleSheet(kit.ui_qss(_TILE_QSS))
            b.clicked.connect(lambda _c=False, site=dict(s): self._on_tile(site))
            self._tiles_lay.insertWidget(i, b, 0, Qt.AlignHCenter)
            self._tiles[name] = b
        self.refresh_active(force=True)

    def refresh_active(self, force=False):
        """高亮当前站点。"""
        name, _hwnd = self._L.active()
        if name == self._active and not force:
            return
        self._active = name
        for key, b in self._tiles.items():
            b.setStyleSheet(kit.ui_qss(_ACTIVE_QSS if key == name else _TILE_QSS))

    # ---------- 交互 ----------
    def _on_tile(self, site):
        open_site_ui(site)

    def _on_add(self):
        SiteManagerDialog(self.window()).focus_new().exec_()
        self.refresh()

    def _on_settings(self):
        """齿轮菜单：聚合AI 的全部设置都在这儿（气泡那边只剩一个「打开」）。"""
        L = self._L
        host = self.window()
        menu = kit.dark_menu(self)
        mg = menu.addAction("管理站点…")
        mg.setData("manage")
        top = menu.addAction("窗口置顶")
        top.setCheckable(True)
        top.setChecked(L.on_top())
        top.setData("top")
        menu.addSeparator()
        cw = menu.addAction("关闭聚合AI")
        cw.setData("close")

        act = menu.exec_(self._set_btn.mapToGlobal(
            self._set_btn.rect().topRight()))
        if act is None:
            return
        kind = act.data()
        if kind == "manage":
            SiteManagerDialog(host).exec_()
            self.refresh()
        elif kind == "top":
            L.set_on_top(not L.on_top())
            if isinstance(host, WebChatHost):
                host.apply_on_top()
        elif kind == "close":
            host.close()


class _Holder(QWidget):
    """网页容器：一个有真实 HWND 的空控件，浏览器窗口就挂在它下面。

    网页没就位时它自己画一条加载提示——浏览器那一秒多里容器是纯色的，
    什么都不说的话看着就像"卡住了 / 打不开"（用户反馈"等了很久，加载时画面
    是空白的"）。
    """

    def __init__(self, host):
        super().__init__(host)
        self._host = host
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setProperty("oi_nozoom", True)
        self.setStyleSheet("background:#14181f;")
        self._loading = ""          # 非空 = 正在加载，显示的是站点名
        self._phase = 0
        self._spin = QTimer(self)
        self._spin.setInterval(120)
        self._spin.timeout.connect(self._tick_spin)

    def set_loading(self, name):
        """name 非空 = 开始显示加载提示；空字符串 = 收起。"""
        name = str(name or "")
        if name == self._loading:
            return
        self._loading = name
        if name:
            self._phase = 0
            if not self._spin.isActive():
                self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def _tick_spin(self):
        self._phase = (self._phase + 1) % 12
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._loading:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() // 2, self.height() // 2
        r = kit.ui(13)
        # 12 格转圈：当前相位最亮，往回依次变暗
        for i in range(12):
            a = 40 + int(200 * (((i - self._phase) % 12) / 11.0))
            p.save()
            p.translate(cx, cy - kit.ui(14))
            p.rotate(i * 30)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(140, 180, 240, 255 - a))
            p.drawRoundedRect(-kit.ui(1), -r - kit.ui(5),
                              kit.ui(2), kit.ui(5), kit.ui(1), kit.ui(1))
            p.restore()
        f = p.font()
        f.setFamily("Microsoft YaHei")
        f.setPixelSize(kit.ui(12))
        p.setFont(f)
        p.setPen(QColor(150, 167, 196))
        p.drawText(0, cy + kit.ui(14), self.width(), kit.ui(20),
                   Qt.AlignHCenter | Qt.AlignTop,
                   "正在打开 %s…" % self._loading)
        p.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._host.refit()


class WebChatHost(QWidget):
    """聚合AI 宿主窗口：左边站点栏，右边嵌一个真实的浏览器应用窗口。"""

    POLL_MS = 600      # 只用来同步标题、发现网页窗口没了；跟随不需要轮询
    REVEAL_MS = 20     # 显示新页面前的对齐重试间隔
    REVEAL_TRIES = 40  # 最多等这么多拍（约 0.8s），到点了也显示，不能一直不出来
    FIT_RETRY_TRIES = 300   # 显示之后继续纠正对齐的拍数（约 6s，够慢站点起来）

    def __init__(self):
        super().__init__(None)
        import webchat_launcher as L
        self._L = L
        self._hwnd = None          # 当前显示的网页窗口
        self._pages = set()        # 已经嵌进容器的所有网页窗口（其余是藏着的）
        self._title = ""
        self.setWindowTitle("聚合AI")
        self.setMinimumSize(kit.ui(360), kit.ui(260))
        try:
            self.setWindowIcon(QApplication.windowIcon())
        except Exception:
            pass
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = WebChatSidebar(self)
        lay.addWidget(self.bar, 0)
        self.holder = _Holder(self)
        lay.addWidget(self.holder, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._tick)
        self.apply_on_top()

    # ---------- 几何 ----------
    def holder_hwnd(self):
        return int(self.holder.winId())

    def holder_size(self):
        """网页容器的物理像素尺寸：浏览器按它开窗，省一次回流。"""
        return self._L.client_size(self.holder_hwnd()) or (960, 720)

    def place_near(self, near):
        """首次打开时摆到桌宠旁边（之后尊重用户自己挪的位置）。"""
        if self.isVisible():
            return
        w, h = self._L.preferred_size()
        x, y, w, h = self._L.compute_placement(near, work_area_of(near), (w, h))
        self.setGeometry(x, y, w, h)

    def apply_on_top(self):
        on = bool(self._L.on_top())
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) == on:
            return
        vis = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if vis:
            self.show()        # 改标志会重建原生窗口，得重新显示

    def set_loading(self, name):
        """容器上的加载提示（网页没就位时显示）。"""
        try:
            self.holder.set_loading(name)
        except Exception:
            pass

    def refit(self):
        """让网页内容正好压住容器（网页窗口是顶层窗口，得我们自己摆）。

        `fit_browser` 是非阻塞的：内缩量还没量到（页面刚起、多进程站点的渲染
        子窗口晚一步出现）时它直接返回 None，绝不在主线程里等——moveEvent 每帧
        都走这里，等 0.25s 就是拖动时整个桌宠卡死。量不到就挂一个���定时器重试，
        直到量到为止。
        """
        if not self._hwnd:
            return
        if self._L.fit_browser(self._hwnd, self.holder_hwnd()) is None:
            self._retry_fit()
        elif getattr(self, "_fit_retry", None) is not None                 and self._fit_retry.isActive():
            self._fit_retry.stop()      # 已经摆好了，重试没必要再跑

    def _retry_fit(self):
        """内缩量还没就绪：隔几十毫秒再试，最多试一小会儿（不阻塞主线程）。"""
        if getattr(self, "_fit_retry", None) is None:
            self._fit_retry = QTimer(self)
            self._fit_retry.setInterval(self.REVEAL_MS)
            self._fit_retry.timeout.connect(self._on_fit_retry)
        # 给足时间：REVEAL_TRIES(40) × REVEAL_MS(20) 只有 0.8 秒，千问/文心
        # 这类站点起得慢，0.8 秒后往往还没就位。这里放到约 6 秒。
        self._fit_left = self.FIT_RETRY_TRIES
        if not self._fit_retry.isActive():
            self._fit_retry.start()

    def _on_fit_retry(self):
        self._fit_left = getattr(self, "_fit_left", 0) - 1
        if not self._hwnd or self._fit_left <= 0:
            self._fit_retry.stop()
            return
        # 摆一次，然后**按"内容是否真的压住容器"判定**是否收工。
        # 只看 fit_browser 的返回值不够：内缩量量到了、摆位也做了，但浏览器
        # 自己可能还在调整窗口（多进程站点常见），这一刻仍然是错位的。
        self._L.fit_browser(self._hwnd, self.holder_hwnd())
        if self._L.fit_ok(self._hwnd, self.holder_hwnd()):
            self._fit_retry.stop()     # 真的对上了，收工

    def moveEvent(self, ev):
        """宿主一动，网页窗口立刻跟上。

        网页窗口是顶层窗口（这样中文输入法才正常），系统不会帮它跟随父窗口，
        所以每个 move 都得自己摆一次——**不要加防抖**，否则拖动时页面会掉队。
        也**不要在这条路径上做任何等待**，理由见 refit。
        """
        super().moveEvent(ev)
        self.refit()

    # ---------- 粘住网页窗口 ----------
    def attach(self, hwnd):
        """把某个站点的网页显示到容器那块区域；之前那个**藏起来，不关**。

        为什么不关：曾经把网页窗口 SetParent 成宿主子窗口时，关掉其中一个会把
        整个浏览器进程带走（实测）。藏起来反而更好——切回去是瞬间的，页面状态
        和写了一半的提问都还在，就是标签页。
        """
        L = self._L
        if not hwnd:
            return False
        if hwnd not in self._pages:
            if not L.glue_browser(hwnd, int(self.winId())):
                return False
            self._pages.add(hwnd)
            # 新页面要等它对齐才显示，这段空窗也给个提示
            self.set_loading(L.window_title(hwnd) or "网页")
        for h in list(self._pages):
            if not L.window_alive(h):
                self._pages.discard(h)
        old = self._hwnd
        self._hwnd = hwnd
        # 关键：先在**隐藏状态下**摆好，摆对了才显示。摆好之前就显示的话，
        # 浏览器自己那条标题栏（带最小化/关闭按钮）会露出来闪一下。
        # 旧页面这期间照常显示着，所以中间也不会出现一片空白。
        self._reveal(hwnd, old, self.REVEAL_TRIES)
        self.bar.refresh_active()
        if not self._timer.isActive():
            self._timer.start()
        return True

    def _reveal(self, hwnd, old, tries):
        """摆位 → 检查对齐 → 对上了才显示；没对上过一会儿再来。"""
        L = self._L
        if self._hwnd != hwnd or hwnd not in self._pages:
            return                      # 期间又切走了，这次作废
        L.fit_browser(hwnd, self.holder_hwnd())
        if not L.fit_ok(hwnd, self.holder_hwnd()) and tries > 0:
            QTimer.singleShot(self.REVEAL_MS,
                              lambda: self._reveal(hwnd, old, tries - 1))
            return
        L.show_browser(hwnd)
        self.set_loading("")        # 页面出来了，收起加载提示
        for h in list(self._pages):
            if h != hwnd:
                L.hide_browser(h)
        L.focus_browser(hwnd)
        QTimer.singleShot(400, self.refit)   # 浏览器晚一步微调时兜一下
        # 等不及了也得显示（不能一直黑着），但**必须继续尝试摆正**：
        # 千问/文心这类多进程站点，渲染子窗口可能 0.8 秒都还没出来，那时
        # 内缩量量不到、页面就停在错位状态，而且以前再没人管它——
        # 于是页面和侧边栏永远错着（用户反馈"主页面跟侧边栏分离、顶部不对齐"）。
        if not L.fit_ok(hwnd, self.holder_hwnd()):
            self._retry_fit()

    # ---------- 事件 ----------
    def changeEvent(self, ev):
        super().changeEvent(ev)
        if ev.type() == QEvent.WindowStateChange:
            # 从最小化恢复回来时重新摆一次：owner 关系只管"一起收起/一起回来"，
            # 位置还得自己对
            if not self.isMinimized():
                QTimer.singleShot(0, self.refit)

    def closeEvent(self, ev):
        L = self._L
        try:
            g = self.geometry()
            L.set_preferred_size(g.width(), g.height())
        except Exception:
            pass
        self._timer.stop()
        pages, self._pages, self._hwnd = list(self._pages), set(), None
        # 顺序要紧：**先藏再解**。owner 窗口被销毁会连带销毁它 own 的窗口，
        # 浏览器会以为自己崩了、下次打开弹"恢复页面"，所以关之前先把 owner
        # 关系解开；解之前先藏起来，免得解开那一瞬间露个面。
        for h in pages:
            try:
                L.hide_browser(h)
                L.unglue_browser(h)
            except Exception:
                pass
        try:
            L.close_all()
        except Exception:
            pass
        super().closeEvent(ev)

    def _tick(self):
        L = self._L
        for h in list(self._pages):
            if not L.window_alive(h):
                self._pages.discard(h)
        if self._hwnd and not L.window_alive(self._hwnd):
            self._hwnd = None
            if self._pages:                 # 还有别的页面：切过去顶上
                self.attach(sorted(self._pages)[0])
            else:
                self.close()                # 一个都不剩了，宿主也收
                return
        if self._hwnd:
            t = L.window_title(self._hwnd)
            if t and t != self._title:
                self._title = t
                self.setWindowTitle("%s · 聚合AI" % t)
        if self._hwnd and not self._L.fit_ok(self._hwnd, self.holder_hwnd()):
            # 兜底自愈：走到这里说明页面和容器还是没对上（浏览器自己改了窗口、
            # 系统 DPI 变了、重试窗口已经过期……）。心跳每 600ms 一次，代价极低，
            # 但保证"错位"不会是个永久状态。
            self.refit()
        self._ensure_page_shown()
        self.bar.refresh_active()

    def _ensure_page_shown(self):
        """不变式：宿主开着、当前页面也活着，那它就**必须**是看得见的。

        用户反馈"点开聚合AI 主区域一片空白"——当前页面被接管了（标题栏都已经
        换成站点名），却没有显示出来。`_reveal` 那条链路有好几种半路作废的情形
        （0.8 秒内又切了站点、接管被重入、显示那一步被跳过……），任何一种都会让
        页面永远藏着，而且之后再没人管它。

        与其逐个堵竞态，不如在心跳里守住这条不变式：不可见就显示出来，顺手把
        别的页面藏好。代价是每 600ms 一次 IsWindowVisible，可以忽略。
        """
        L = self._L
        h = self._hwnd
        if not h or not self.isVisible() or self.isMinimized():
            return
        try:
            if L.window_visible(h):
                return
            L.show_browser(h)
            self.refit()
            for other in list(self._pages):
                if other != h:
                    L.hide_browser(other)
        except Exception:
            pass


_HOST = None


def host(create=True):
    global _HOST
    if _HOST is None and create:
        _HOST = WebChatHost()
    return _HOST


def show_sidebar():
    """兼容老调用：确保宿主在、并把当前网页窗口接管进来。"""
    h = host()
    _name, hwnd = None, None
    try:
        import webchat_launcher as L
        _name, hwnd = L.active()
    except Exception:
        pass
    if hwnd:
        h.attach(hwnd)
    return h


def hide_sidebar():
    if _HOST is not None:
        try:
            _HOST.close()
        except Exception:
            pass


def refresh_sidebar():
    if _HOST is not None:
        try:
            _HOST.bar.refresh()
        except Exception:
            pass
