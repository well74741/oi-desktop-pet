# -*- coding: utf-8 -*-
"""H5 卡片扩展基础（基础设施先行，功能后续迭代开发）。

当前提供：
- H5Card          卡片数据模型（kind: html / text / image / list / action）
- CardRegistry    kind -> 构建函数注册表，支持后续注册自定义卡片类型
- CardView        单卡片宿主（默认 QTextBrowser 富文本渲染）
- CardHost        卡片容器：竖向堆叠多张卡片、细滚动条、与气泡风格一致
- H5Bridge        前端 <-> Python 信号桥（后续给 Web 渲染注入 JS 时使用）
- enable_webengine()  可选开启 QWebEngine 渲染（需要安装 PyQtWebEngine，
                      且必须在 QApplication 创建前调用，重启后生效）

后续开发入口示例：
    from h5_cards import H5Card, CardHost
    host = CardHost()
    host.show_cards([
        H5Card(kind="text", title="服务器", text="CPU 45% · 内存 62%"),
        H5Card(kind="html", title="公告", html="<b>今日维护</b> 23:00 开始"),
    ])
    # 气泡内直接调用： status_bubble.show_cards([...])
"""

import os
import re
import time

from PyQt5.QtCore import Qt, QObject, QTimer, QRectF, pyqtSignal
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTextBrowser, QListWidget, QListWidgetItem, QFrame, QSizePolicy,
)


# ==================== 配置 ====================

_WEBENGINE = False      # 默认关闭 Web 渲染（未安装 PyQtWebEngine，安全回退富文本）
# 注意：不在此模块级 import QtWebEngineWidgets——webchat 已拆到独立子进程，
# 主进程不应加载 Chromium；只有显式 enable_webengine() 才惰性加载。


def webengine_available():
    """PyQtWebEngine 是否已安装（惰性探测，不加载 Chromium）。"""
    try:
        from PyQt5 import QtWebEngineWidgets  # noqa: F401
        return True
    except Exception:
        return False


def enable_webengine():
    """开启 Web 渲染（实验性）。

    注意：必须在创建 QApplication 之前调用并重启桌宠；QWebEngine 需要
    AA_ShareOpenGLContexts 属性，运行中开启会导致崩溃。
    """
    global _WEBENGINE
    try:
        from PyQt5 import QtWebEngineWidgets  # noqa: F401
    except Exception:
        raise RuntimeError("PyQtWebEngine 未安装：pip install PyQtWebEngine")
    _WEBENGINE = True


_SCROLL_QSS = (
    "QScrollArea{background:transparent;border:none;}"
    "QScrollArea > QWidget > QWidget{background:transparent;}"
    "QScrollBar:vertical{background:transparent;width:4px;margin:0;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,75);"
    "border-radius:2px;min-height:16px;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)
_TEXT_QSS = (
    "QTextBrowser{background:transparent;border:none;color:#e8ecf5;"
    "font-size:10px;padding:1px 2px;}"
    "QScrollBar:vertical{background:transparent;width:4px;margin:0;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,75);"
    "border-radius:2px;min-height:16px;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)
_TEXT_LAB_QSS = (
    "QLabel{background:transparent;color:#e8ecf5;font-size:10px;padding:1px 2px;}"
)
_LIST_QSS = (
    "QListWidget{background:rgba(255,255,255,18);border:none;border-radius:4px;"
    "color:#e8ecf5;font-size:10px;padding:1px 2px;}"
    "QListWidget::item{padding:1px 2px;}"
    "QListWidget::item:selected{background:rgba(74,144,226,90);}"
    "QScrollBar:vertical{background:transparent;width:4px;margin:0;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,75);"
    "border-radius:2px;min-height:16px;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)
_IMG_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s\"'<>]*)?", re.I)
_HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")


# ==================== 数据模型 ====================

class H5Card:
    """一张 H5 卡片的数据模型。kind 决定渲染器：html / text / image / list / action。"""

    def __init__(self, card_id="", title="", kind="text", html="", text="",
                 image="", items=None, actions=None, width=0, height=0):
        self.id = card_id
        self.title = title
        self.kind = kind
        self.html = html
        self.text = text
        self.image = image
        self.items = items or []
        self.actions = actions or []   # [{"label":..., "key":..., "data":...}]
        self.width = width
        self.height = height


# ==================== 注册表 ====================

class CardRegistry:
    """kind -> 构建函数的注册表；后续自定义卡片类型只需注册一个构建函数。"""

    _builders = {}

    @classmethod
    def register(cls, kind):
        def deco(fn):
            cls._builders[kind] = fn
            return fn
        return deco

    @classmethod
    def build(cls, card):
        fn = cls._builders.get(card.kind)
        if fn is None:
            return CardView(card)
        return fn(card)

    @classmethod
    def kinds(cls):
        return sorted(cls._builders.keys())


# ==================== 桥接 ====================

class H5Bridge(QObject):
    """前端 <-> Python 信号桥（后续给 Web 渲染注入 JS 时使用）。"""

    action_triggered = pyqtSignal(str, dict)   # (key, data)
    open_url = pyqtSignal(str)

    def call_action(self, key, data=None):
        self.action_triggered.emit(key, data or {})

    def open(self, url):
        self.open_url.emit(url)


# ==================== 卡片渲染 ====================

class CardView(QWidget):
    """单卡片宿主：按 kind 选择渲染器。"""

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.card = card
        self.bridge = H5Bridge(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        if card.title:
            t = QLabel(card.title)
            t.setStyleSheet("color:#cfe0ff;font-size:10px;padding:0 2px;")
            lay.addWidget(t)
        lay.addWidget(self._build_body(card), 1)

    def _build_body(self, card):
        kind = card.kind
        if kind == "image":
            lab = QLabel()
            lab.setAlignment(Qt.AlignCenter)
            pm = QPixmap(card.image)
            if not pm.isNull():
                w = card.width or 160
                h = max(card.height or 90, 60)
                lab.setPixmap(pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return lab
        if kind == "list":
            lw = QListWidget()
            lw.setStyleSheet(_LIST_QSS)
            for it in card.items:
                if isinstance(it, dict):
                    txt = "%s：%s" % (it.get("name", ""), it.get("value", ""))
                else:
                    txt = str(it)
                lw.addItem(QListWidgetItem(txt))
            return lw
        # html / action：富文本浏览器（结构化内容，保留链接）
        if kind == "html" and card.html:
            tb = QTextBrowser()
            tb.setStyleSheet(_TEXT_QSS)
            tb.setFrameShape(QTextBrowser.NoFrame)
            tb.setOpenExternalLinks(True)
            tb.viewport().setAutoFillBackground(False)
            tb.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
            tb.setHtml(card.html)
            return tb
        # text：居中 QLabel（横向+纵向都居中，测试窗口内容不再偏上）
        lab = QLabel(card.text)
        lab.setStyleSheet(_TEXT_LAB_QSS)
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(True)
        return lab


@CardRegistry.register("html")
def _build_html(card):
    return CardView(card)


@CardRegistry.register("text")
def _build_text(card):
    return CardView(card)


@CardRegistry.register("image")
def _build_image(card):
    return CardView(card)


@CardRegistry.register("list")
def _build_list(card):
    return CardView(card)


@CardRegistry.register("action")
def _build_action(card):
    return CardView(card)


@CardRegistry.register("pomodoro")
def _build_pomodoro(card):
    return PomodoroCard(card)


# ==================== 卡片容器 ====================

class PomodoroState:
    """番茄时钟全局状态：工作 25 分钟 / 休息 5 分钟，自动轮换。"""

    WORK = 25 * 60
    BREAK = 5 * 60
    _mode = "work"          # work / break
    _remaining = float(WORK)
    _running = False
    _last_t = 0.0

    @classmethod
    def tick(cls):
        if cls._running:
            now = time.monotonic()
            cls._remaining = max(0.0, cls._remaining - (now - cls._last_t))
            cls._last_t = now
            if cls._remaining <= 0:
                cls._switch()
        return cls._remaining

    @classmethod
    def _switch(cls):
        if cls._mode == "work":
            cls._mode, cls._remaining = "break", float(cls.BREAK)
        else:
            cls._mode, cls._remaining = "work", float(cls.WORK)
        cls._running = False

    @classmethod
    def start(cls):
        cls.tick()
        if not cls._running:
            cls._running = True
            cls._last_t = time.monotonic()

    @classmethod
    def pause(cls):
        cls.tick()
        cls._running = False

    @classmethod
    def reset(cls):
        cls._running = False
        cls._mode = "work"
        cls._remaining = float(cls.WORK)

    @classmethod
    def mode(cls):
        cls.tick()
        return cls._mode

    @classmethod
    def current_text(cls):
        cls.tick()
        m, s = divmod(max(0, int(round(cls._remaining))), 60)
        if cls._mode == "work":
            return "工作中 %02d:%02d" % (m, s)
        return "休息 %02d:%02d" % (m, s)


class PomodoroCard(QWidget):
    """番茄时钟交互卡片：倒计时 + 开始/暂停/重置；状态绑定规则脚本的 state 字典。"""

    FIX_H = 72
    _QSS = (
        "QPushButton{border:none;background:rgba(255,255,255,35);border-radius:4px;"
        "color:#e8ecf5;font-family:SimHei;font-size:10px;padding:2px 8px;}"
        "QPushButton:hover{background:rgba(255,255,255,70);}"
        "QPushButton:pressed{background:rgba(74,144,226,120);}"
    )

    def __init__(self, state=None, card=None, parent=None):
        super().__init__(parent)
        self.card = card
        self.state = state if state is not None else {}
        self.state.setdefault("mode", "work")
        self.state.setdefault("remaining", 1500.0)
        self.state.setdefault("running", False)
        self.state.setdefault("last", time.monotonic())
        self.state.setdefault("work", 25 * 60)
        self.state.setdefault("break", 5 * 60)
        self.setFixedHeight(self.FIX_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)   # 底部留白，按钮不贴卡片下框
        lay.setSpacing(1)
        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignCenter)
        f = QFont("SimHei")
        f.setPointSize(16)
        f.setBold(True)
        self.time_label.setFont(f)
        self.time_label.setStyleSheet("color:#e8ecf5;")
        lay.addWidget(self.time_label, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)   # 让按钮组水平居中
        self.toggle_btn = QPushButton("开始")
        self.reset_btn = QPushButton("重置")
        for b in (self.toggle_btn, self.reset_btn):
            b.setStyleSheet(self._QSS)
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        self.mode_label = QLabel("工作")
        self.mode_label.setFont(QFont("SimHei", 10))
        self.mode_label.setStyleSheet("color:#a8e6a3;font-size:10px;")
        row.addWidget(self.mode_label)
        row.addStretch(1)   # 右侧对称拉伸，按钮组居中
        lay.addLayout(row)
        self.toggle_btn.clicked.connect(self._toggle)
        self.reset_btn.clicked.connect(self._reset)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _toggle(self):
        st = self.state
        self._tick()
        if st.get("running"):
            st["running"] = False
            self.toggle_btn.setText("继续")
        else:
            st["running"] = True
            st["last"] = time.monotonic()
            self.toggle_btn.setText("暂停")
        self._refresh()

    def _reset(self):
        st = self.state
        st["running"] = False
        st["mode"] = "work"
        st["remaining"] = float(st.get("work", 1500))
        self.toggle_btn.setText("开始")
        self._refresh()

    def _tick(self):
        try:
            st = self.state
            if st.get("running"):
                now = time.monotonic()
                st["remaining"] = max(0.0, float(st.get("remaining", 0))
                                      - (now - float(st.get("last", now))))
                st["last"] = now
                if st["remaining"] <= 0:
                    if st.get("mode") == "work":
                        st["mode"], st["remaining"] = "break", float(st.get("break", 300))
                    else:
                        st["mode"], st["remaining"] = "work", float(st.get("work", 1500))
                    st["running"] = False
        except Exception:
            pass

    def _refresh(self):
        try:
            self._tick()
            st = self.state
            mode = st.get("mode", "work")
            m, s = divmod(max(0, int(round(float(st.get("remaining", 0))))), 60)
            self.time_label.setText(
                ("工作中 %02d:%02d" % (m, s)) if mode == "work"
                else ("休息 %02d:%02d" % (m, s)))
            self.mode_label.setText("工作" if mode == "work" else "休息")
            self.mode_label.setStyleSheet(
                "color:#a8e6a3;font-size:10px;" if mode == "work"
                else "color:#ffd27d;font-size:10px;")
        except Exception:
            pass


class CardHost(QScrollArea):
    """卡片容器：竖向堆叠多张卡片，细滚动条，与气泡风格一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(_SCROLL_QSS)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._container.setAutoFillBackground(False)
        self._lay = QVBoxLayout(self._container)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)
        self.setWidget(self._container)
        self._cards = []

    def show_cards(self, cards):
        """替换显示一组卡片（清空旧的）。"""
        for w in list(self._cards):
            self._lay.removeWidget(w)
            w.deleteLater()
        self._cards = []
        for card in cards:
            view = CardRegistry.build(card)
            self._lay.addWidget(view)
            self._cards.append(view)

    def add_card(self, card):
        view = CardRegistry.build(card)
        self._lay.addWidget(view)
        self._cards.append(view)
        return view

    def add_widget(self, widget):
        """直接放入一个现成组件（如番茄卡片），用于测试结果区与气泡保持一致。"""
        self._lay.addWidget(widget)
        self._cards.append(widget)
        return widget

    def clear(self):
        self.show_cards([])

    def cards(self):
        return list(self._cards)

    def heightHint(self):
        """卡片内容总高度（供气泡布局预留空间）。"""
        h = 0
        for w in self._cards:
            h += max(w.height(), w.sizeHint().height())
        if self._cards:
            h += (len(self._cards) - 1) * max(0, self._lay.spacing())
        return h


# ==================== 演示/自检 ====================

def demo_cards():
    """构建一组示例卡片，用于验证 H5 拓展基础链路。"""
    return [
        H5Card(kind="html", title="今日提示",
               html="<b>记住喝水</b> ☕ 25°C 晴，适合出门走走"),
        H5Card(kind="text", title="服务器",
               text="CPU 45% · 内存 62% · 磁盘 78%"),
        H5Card(kind="list", title="待办",
               items=[{"name": "开会", "value": "10:30"},
                      {"name": "提交周报", "value": "今天"},
                      {"name": "给桌宠加新功能", "value": "随时"}]),
        H5Card(kind="image", title="示例图片",
               image=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "assets", "oi.png"),
               width=96, height=96),
    ]


def result_cards(val, error=""):
    """把规则测试结果转成 H5 卡片列表（兼容文本 / HTML / 图片链接等格式）。"""
    cards = []
    if error:
        if str(val):
            cards.append(H5Card(kind="text", title="错误",
                                text="%s（原因：%s）" % (val, error)))
        else:
            cards.append(H5Card(kind="text", title="提示", text=str(error)))
        return cards
    s = str(val)
    if _IMG_RE.search(s):
        # 图片链接：转成 <img> 嵌入 HTML 展示
        s = _IMG_RE.sub(lambda m: "<img src='%s' width='150'/>" % m.group(0), s)
    if _HTML_RE.search(s):
        cards.append(H5Card(kind="html", title="结果", html=s))
    else:
        cards.append(H5Card(kind="text", title="结果", text=s))
    return cards


class ResultView(QWidget):
    """规则测试结果查看器：兼容文本 / HTML / 图片等 H5 格式（设置与编辑窗口共用）。"""

    def __init__(self, parent=None, pin_size=None):
        super().__init__(parent)
        self._pin_size = pin_size
        if pin_size is not None:
            self.setFixedSize(pin_size)
        self.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.host = CardHost(self)
        lay.addWidget(self.host, 1)
        self.placeholder = QLabel("测试窗口")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet(
            "color:#aab3c5;font-size:10px;border:none;background:transparent;")
        lay.addWidget(self.placeholder)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(28, 32, 44, 235))
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 6, 6)
        p.end()

    def show_result(self, val, error=""):
        cards = result_cards(val, error)
        self.host.show_cards(cards)
        self.placeholder.setVisible(not cards)
        if len(cards) == 1:
            # 单条结果显示时垂直居中，避免内容偏上
            try:
                self.host._lay.setAlignment(self.host._cards[0],
                                            Qt.AlignHCenter | Qt.AlignVCenter)
            except Exception:
                pass
        else:
            for v in self.host._cards:
                try:
                    self.host._lay.setAlignment(v, Qt.AlignHCenter)
                except Exception:
                    pass
        if self._pin_size is None:
            try:
                # 恢复灵活高度（显示文本结果时测试区可再拉伸）
                self.setMinimumHeight(80)
                self.setMaximumHeight(16777215)
            except Exception:
                pass

    def show_widget(self, widget):
        """直接显示一个交互组件（与气泡内渲染一致），如番茄卡片。"""
        self.placeholder.hide()
        self.host.show_cards([])
        self.host.add_widget(widget)
        try:
            # 与气泡卡片内容一致：固定宽度 + 忽略固有尺寸策略 + 居中
            if widget.width() < 100 or widget.width() > 300:
                widget.setFixedWidth(184)
            widget.setSizePolicy(QSizePolicy.Ignored, widget.sizePolicy().verticalPolicy())
            host_h = self.host.viewport().height() if self.host.viewport() else self.height()
            if widget.height() <= max(10, host_h - 4):
                # 组件能完整放下时垂直居中
                self.host._lay.setAlignment(widget, Qt.AlignHCenter | Qt.AlignVCenter)
            else:
                # 组件高于测试框：顶对齐，避免内容上移裁切、按钮顶到框边
                self.host._lay.setAlignment(widget, Qt.AlignHCenter | Qt.AlignTop)
        except Exception:
            pass
        widget.show()

    def clear(self):
        self.host.show_cards([])
        self.placeholder.show()
        if self._pin_size is None:
            try:
                self.setMinimumHeight(80)
                self.setMaximumHeight(16777215)
            except Exception:
                pass
