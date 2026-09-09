# -*- coding: utf-8 -*-
"""聚合 AI 悬浮窗口：左侧模型列表（可拖动/删除/添加/收起）+ 右侧完整网页。

深色科技感 + 黑体。站点列表存 webchat_sites.json，登录态由 QtWebEngine
默认档案持久化。边缘热区显示调整光标并支持拖拽调大小；最小化回任务栏。
"""
import data_store
import os
import sys

from PyQt5.QtCore import Qt, QPoint, QUrl, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QListWidget,
                             QListWidgetItem, QPushButton, QMenu, QDialog,
                             QLabel, QLineEdit, QDialogButtonBox)

_SITES_FILE = "webchat_sites.json"
_DEFAULT_SITES = [
    {"name": "DeepSeek", "url": "https://chat.deepseek.com"},
    {"name": "豆包", "url": "https://www.doubao.com/chat/"},
    {"name": "通义千问", "url": "https://www.qianwen.com/"},
    {"name": "Kimi", "url": "https://www.kimi.com/"},
    {"name": "文心一言", "url": "https://yiyan.baidu.com/"},
    {"name": "腾讯元宝", "url": "https://yuanbao.tencent.com/chat"},
    {"name": "讯飞星火", "url": "https://xinghuo.xfyun.cn/desk"},
]

_EDGE_M = 7   # 边缘热区宽度


def _asset_path(name):
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets", name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", name)


def panel_icon():
    p = _asset_path("webchat_icon.png")
    return QIcon(p) if os.path.exists(p) else QIcon()


def load_sites():
    sites = data_store.read_list(_SITES_FILE)
    if not sites:
        return list(_DEFAULT_SITES)
    # 1) 移除已被下线的旧内置站点（如智谱清言），保留用户自定义站点
    default_names = {d["name"] for d in _DEFAULT_SITES}
    builtin_names = {"DeepSeek", "豆包", "通义千问", "智谱清言", "文心一言",
                     "Kimi", "腾讯元宝", "讯飞星火"}
    merged = [s for s in sites
              if not (isinstance(s, dict) and s.get("name") in builtin_names
                      and s.get("name") not in default_names)]
    # 2) 内置主流站点缺失时自动补入（保留用户已有顺序）
    names = {s.get("name") for s in merged if isinstance(s, dict)}
    for d in _DEFAULT_SITES:
        if d["name"] not in names:
            merged.append(dict(d))
    if len(merged) != len(sites):
        save_sites(merged)
    return merged


def save_sites(sites):
    data_store.write_json(_SITES_FILE, sites)


_FONT = "Microsoft YaHei, SimHei"

_QSS = """
QWidget{font-family:%s;color:#d7e0ee;}
QWidget#WebChatPanel{background:#141a26;}
QWidget#titlebar{background:#1b2230;border-bottom:1px solid #232b3a;}
QLabel#title{color:#e8ecf5;font-size:12px;font-weight:600;}
QPushButton#winbtn{background:transparent;border:none;color:#9fb0c8;font-size:13px;padding:0;}
QPushButton#winbtn:hover{background:rgba(255,255,255,28);border-radius:4px;}
QPushButton#closebtn:hover{background:#e0433f;border-radius:4px;color:#fff;}
QWidget#sidebar{background:#181f2d;border-right:1px solid #232b3a;}
QLabel#sidetitle{color:#8fa3c0;font-size:10px;font-weight:600;}
QPushButton#foldbtn{background:rgba(255,255,255,26);border:none;color:#c6d2e2;
    font-size:10px;border-radius:3px;}
QPushButton#foldbtn:hover{background:rgba(74,144,226,110);}
QPushButton#addbtn{background:rgba(255,255,255,12);border:1px solid #2c3649;
    border-radius:6px;color:#cdd6e4;height:30px;font-size:11px;margin:3px 5px;}
QPushButton#addbtn:hover{border-color:#3d4c66;background:rgba(255,255,255,16);}
QListWidget{background:transparent;border:none;color:#cdd6e4;font-size:11px;
    outline:none;padding:2px;}
QListWidget::item{height:30px;border:1px solid #2c3649;border-radius:6px;
    padding:0 6px;margin:3px 5px;}
QListWidget::item:hover{border-color:#3d4c66;background:rgba(255,255,255,12);}
QListWidget::item:selected{border-color:#4a90e2;background:rgba(74,144,226,70);
    color:#ffffff;}
QMenu{background:#1e2636;color:#e8ecf5;border:1px solid rgba(255,255,255,45);
    border-radius:6px;padding:3px;font-family:%s;}
QMenu::item{padding:6px 20px;font-size:11px;border-radius:4px;}
QMenu::item:selected{background:rgba(74,144,226,130);}
QDialog{background:#1a2130;font-family:%s;}
QDialog QLabel{color:#8fa3c0;font-size:11px;}
QDialog QLineEdit{background:#121824;border:1px solid #2c3649;border-radius:6px;
    color:#e8ecf5;padding:6px 8px;font-size:11px;}
QDialog QLineEdit:focus{border-color:#4a90e2;}
QDialog QPushButton{border:none;border-radius:5px;padding:5px 14px;font-size:11px;
    background:rgba(255,255,255,30);color:#e8ecf5;}
QDialog QPushButton:hover{background:rgba(255,255,255,55);}
QDialog QPushButton#okbtn{background:#4a90e2;color:#fff;}
QDialog QPushButton#okbtn:hover{background:#5aa0eb;}
""" % (_FONT, _FONT, _FONT)


class _EdgeHandle(QWidget):
    """窗口边缘/角落热区：显示调整光标并处理拖拽改大小。"""

    _CURSORS = {1: Qt.SizeHorCursor, 2: Qt.SizeHorCursor,
                3: Qt.SizeVerCursor, 4: Qt.SizeVerCursor,
                5: Qt.SizeFDiagCursor, 6: Qt.SizeBDiagCursor,
                7: Qt.SizeBDiagCursor, 8: Qt.SizeFDiagCursor}

    def __init__(self, parent, edge):
        super().__init__(parent)
        self._edge = edge
        self._drag = None
        self.setCursor(self._CURSORS.get(edge, Qt.ArrowCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (self.parentWidget().geometry(), e.globalPos())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        g, g0 = self._drag
        dx = e.globalPos().x() - g0.x()
        dy = e.globalPos().y() - g0.y()
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        p = self.parentWidget()
        mw, mh = p.minimumWidth(), p.minimumHeight()
        ed = self._edge
        if ed in (1, 5, 7):
            x = g.x() + dx
            w = g.width() - dx
        if ed in (2, 6, 8):
            w = g.width() + dx
        if ed in (3, 5, 6):
            y = g.y() + dy
            h = g.height() - dy
        if ed in (4, 7, 8):
            h = g.height() + dy
        if w >= mw and h >= mh:
            p.setGeometry(x, y, w, h)
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None
        e.accept()


class _AddDialog(QDialog):
    """添加/编辑模型站点：命名 + 网址 + 测试网页（非模态，不锁其他窗口）。"""

    saved = pyqtSignal(dict)

    def __init__(self, parent=None, site=None):
        super().__init__(parent)
        self.setWindowTitle("添加模型" if site is None else "编辑模型")
        self.setWindowFlags(Qt.Dialog)
        self.setModal(False)
        self.setStyleSheet(_QSS)
        self.setFixedWidth(320)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(8)

        lay.addWidget(QLabel("命名"))
        self.name_edit = QLineEdit(site.get("name", "") if site else "")
        self.name_edit.setPlaceholderText("如：豆包")
        lay.addWidget(self.name_edit)

        lay.addWidget(QLabel("站点网址"))
        self.url_edit = QLineEdit(site.get("url", "") if site else "")
        self.url_edit.setPlaceholderText("https://...")
        lay.addWidget(self.url_edit)

        test_btn = QPushButton("测试网页（默认浏览器打开）")
        test_btn.clicked.connect(self._test)
        lay.addWidget(test_btn)

        btns = QDialogButtonBox()
        ok = btns.addButton("确定", QDialogButtonBox.AcceptRole)
        ok.setObjectName("okbtn")
        cancel = btns.addButton("取消", QDialogButtonBox.RejectRole)
        ok.clicked.connect(self._on_ok)
        cancel.clicked.connect(self.reject)
        lay.addWidget(btns)

    def _test(self):
        import webbrowser
        url = self.url_edit.text().strip()
        if url:
            webbrowser.open(url)

    def _on_ok(self):
        r = {"name": self.name_edit.text().strip(),
             "url": self.url_edit.text().strip()}
        if r["name"] and r["url"]:
            self.saved.emit(r)
            self.accept()


class WebChatPanel(QWidget):
    _SIDEBAR_W = 150
    _SIDEBAR_COLLAPSED_W = 38

    def __init__(self):
        super().__init__(None)
        self.setObjectName("WebChatPanel")
        self.setWindowFlags(Qt.FramelessWindowHint)   # 不置顶、可最小化回任务栏
        self.setWindowTitle("聚合AI")
        self.setWindowIcon(panel_icon())
        self.setStyleSheet(_QSS)
        self.setFont(QFont("Microsoft YaHei", 10))
        self.resize(1280, 720)
        self.setMinimumSize(640, 420)
        self._sites = load_sites()
        self._collapsed = False
        self._moving = False
        self._drag_global = None
        self._saved_geom = None
        self._edges = []
        self._build_ui()
        self._reload_list()
        if self._sites:
            self._switch_to(0)

    # ---------- UI ----------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 标题栏
        bar = QWidget(self)
        bar.setObjectName("titlebar")
        bar.setFixedHeight(34)
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(12, 0, 8, 0)
        bh.setSpacing(4)
        lab = QLabel("聚合AI", bar)
        lab.setObjectName("title")
        bh.addWidget(lab, 1)
        ext_btn = QPushButton("外开", bar)
        ext_btn.setObjectName("winbtn")
        ext_btn.setFixedSize(34, 24)
        ext_btn.setToolTip("用默认浏览器打开当前站点（旧内核打不开时用）")
        ext_btn.clicked.connect(self._open_external)
        bh.addWidget(ext_btn)
        min_btn = QPushButton("—", bar)
        min_btn.setObjectName("winbtn")
        min_btn.setFixedSize(26, 24)
        min_btn.setToolTip("最小化")
        min_btn.clicked.connect(self.showMinimized)
        bh.addWidget(min_btn)
        close_btn = QPushButton("✕", bar)
        close_btn.setObjectName("closebtn")
        close_btn.setFixedSize(26, 24)
        close_btn.setToolTip("关闭（隐藏）")
        close_btn.clicked.connect(self.hide)
        bh.addWidget(close_btn)
        outer.addWidget(bar)

        body = QWidget(self)
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # 左侧边栏
        self.sidebar = QWidget(body)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(self._SIDEBAR_W)
        sv = QVBoxLayout(self.sidebar)
        sv.setContentsMargins(0, 8, 0, 8)
        sv.setSpacing(0)

        st = QLabel("模型", self.sidebar)
        st.setObjectName("sidetitle")
        st.setContentsMargins(10, 0, 0, 4)
        sv.addWidget(st)

        self.list = QListWidget(self.sidebar)
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.customContextMenuRequested.connect(self._on_menu)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.model().rowsMoved.connect(self._on_reorder)
        sv.addWidget(self.list, 1)

        self._add_btn = QPushButton("＋ 添加模型", self.sidebar)
        self._add_btn.setObjectName("addbtn")
        self._add_btn.clicked.connect(self._add_site)
        sv.addWidget(self._add_btn)
        h.addWidget(self.sidebar)

        # 悬浮收纳箭头（侧栏右侧、略靠右不重叠边缘）
        self._fold_btn = QPushButton("◀", self)
        self._fold_btn.setObjectName("foldbtn")
        self._fold_btn.setFixedSize(15, 36)
        self._fold_btn.setToolTip("收起/展开侧栏")
        self._fold_btn.setCursor(Qt.PointingHandCursor)
        self._fold_btn.clicked.connect(self._toggle_sidebar)
        self._place_fold_btn()

        # 右侧网页
        from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile
        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.ForcePersistentCookies)
        # QtWebEngine(Chromium 83) 内核过旧，个别网站会提示"浏览器版本过低"：
        # 用新版 Chrome 的 UA 伪装，多数站点仅按 UA 判断即可通过。
        # （真正依赖新内核 API 的站点仍建议用默认浏览器打开）
        try:
            profile.setHttpUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        except Exception:
            pass
        self.web = QWebEngineView(body)
        h.addWidget(self.web, 1)

        outer.addWidget(body, 1)

        # 边缘热区（覆盖网页区域的边缘，显示调整光标）
        self._create_edge_handles()

    def _create_edge_handles(self):
        for ed in range(1, 9):
            hw = _EdgeHandle(self, ed)
            self._edges.append(hw)
        self._update_edge_handles()

    def _update_edge_handles(self):
        m = _EDGE_M
        w, h = self.width(), self.height()
        geo = {
            1: (0, 0, m, h),          # 左
            2: (w - m, 0, m, h),      # 右
            3: (0, 0, w, m),          # 上
            4: (0, h - m, w, m),      # 下
            5: (0, 0, m, m),          # 左上
            6: (w - m, 0, m, m),      # 右上
            7: (0, h - m, m, m),      # 左下
            8: (w - m, h - m, m, m),  # 右下
        }
        for hw in self._edges:
            hw.setGeometry(*geo[hw._edge])
            hw.raise_()

    def _place_fold_btn(self):
        x = 1 + (self._SIDEBAR_COLLAPSED_W if self._collapsed else self._SIDEBAR_W)
        self._fold_btn.move(x + 2, max(44, (self.height() - 36) // 2))
        self._fold_btn.raise_()

    # ---------- 侧栏收起 ----------
    def _open_external(self):
        """用默认浏览器打开当前站点（旧内核打不开的站点兜底）。"""
        try:
            import webbrowser
            row = self.list.currentRow()
            url = ""
            if 0 <= row < len(self._sites):
                url = self._sites[row].get("url", "")
            if not url:
                url = self.web.url().toString()
            if url:
                webbrowser.open(url)
        except Exception:
            pass

    def _toggle_sidebar(self):
        self._collapsed = not self._collapsed
        w = self._SIDEBAR_COLLAPSED_W if self._collapsed else self._SIDEBAR_W
        self.sidebar.setFixedWidth(w)
        self._fold_btn.setText("▶" if self._collapsed else "◀")
        self._add_btn.setText("＋" if self._collapsed else "＋ 添加模型")
        self._reload_list()
        self._place_fold_btn()

    # ---------- 站点列表 ----------
    def _reload_list(self):
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for s in self._sites:
            name = s.get("name", s.get("url", "?"))
            it = QListWidgetItem(name[:1] if self._collapsed else name)
            it.setData(Qt.UserRole, s.get("url", ""))
            it.setTextAlignment(Qt.AlignCenter)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if 0 <= cur < self.list.count():
            self.list.setCurrentRow(cur)

    def _on_row_changed(self, row):
        if 0 <= row < len(self._sites):
            self._switch_to(row)

    def _switch_to(self, idx):
        if 0 <= idx < len(self._sites):
            url = self._sites[idx].get("url", "")
            if url and self.web.url().toString() != url:
                self.web.load(QUrl(url))

    def _on_reorder(self, *a):
        order = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            order.append({"name": it.text(), "url": it.data(Qt.UserRole)})
        if order:
            self._sites = order
            save_sites(self._sites)

    def _on_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("编辑")
        act_del = menu.addAction("删除")
        act = menu.exec_(self.list.mapToGlobal(pos))
        row = self.list.row(item)
        if act == act_del and 0 <= row < len(self._sites):
            self._sites.pop(row)
            save_sites(self._sites)
            self._reload_list()
        elif act == act_edit and 0 <= row < len(self._sites):
            dlg = _AddDialog(self, site=self._sites[row])
            dlg.saved.connect(
                lambda r, idx=row: self._apply_site(idx, r))
            dlg.show()

    def _apply_site(self, idx, r):
        if 0 <= idx < len(self._sites):
            self._sites[idx] = r
            save_sites(self._sites)
            self._reload_list()

    def _add_site(self):
        dlg = _AddDialog(self)
        dlg.saved.connect(self._on_site_added)
        dlg.show()

    def _on_site_added(self, r):
        self._sites.append(r)
        save_sites(self._sites)
        self._reload_list()
        self.list.setCurrentRow(len(self._sites) - 1)

    # ---------- 标题栏拖动 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.y() < 34:
            self._moving = True
            self._drag_global = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._moving and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag_global)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._moving = False
        super().mouseReleaseEvent(e)

    # ---------- 尺寸保持 ----------
    def showEvent(self, e):
        if self._saved_geom is not None:
            self.setGeometry(self._saved_geom)
            self._saved_geom = None
        # 窗口显示后再加载：隐藏窗口上 WebEngine 的 load 可能不生效，
        # 打开面板时若当前没有页面则加载选中项（默认第一个站点）
        try:
            if self.web.url().isEmpty() or self.web.url().toString() == "about:blank":
                self._switch_to(self.list.currentRow() if self.list.currentRow() >= 0 else 0)
        except Exception:
            pass
        super().showEvent(e)

    def hideEvent(self, e):
        self._saved_geom = self.geometry()
        super().hideEvent(e)
        for cb in list(_hidden_cbs):
            try:
                cb()
            except Exception:
                pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_edge_handles()
        self._place_fold_btn()


_panel = None
_hidden_cbs = []


def on_panel_hidden(cb):
    """面板被隐藏（点 ✕）时回调：气泡用它把按钮文字切回"打开"。"""
    if cb not in _hidden_cbs:
        _hidden_cbs.append(cb)


def remove_panel_hidden_callback(cb):
    """气泡热重建时移除旧回调，避免后续调用已销毁的 Qt 对象。"""
    while cb in _hidden_cbs:
        _hidden_cbs.remove(cb)


def show_panel():
    """打开（或复用）聚合 AI 悬浮窗口。"""
    global _panel
    if _panel is None:
        _panel = WebChatPanel()
    _panel.show()
    _panel.raise_()
    _panel.activateWindow()
    return _panel


def hide_panel():
    """隐藏悬浮窗口（保留尺寸与站点状态）。"""
    global _panel
    if _panel is not None:
        _panel.hide()


def toggle_panel():
    """打开 <-> 关闭切换，返回切换后的可见状态。"""
    global _panel
    if _panel is not None and _panel.isVisible():
        _panel.hide()
        return False
    show_panel()
    return True


def is_panel_open():
    """面板当前是否可见（打开状态）。"""
    global _panel
    return _panel is not None and _panel.isVisible()
