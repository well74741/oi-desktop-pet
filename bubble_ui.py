# -*- coding: utf-8 -*-
"""气泡 UI 模块：聊天面板 ChatPanel 与气泡窗口 StatusBubble。

从 status_monitor.py 拆出，方便独立维护；状态采集/规则/弹出气泡等后端
仍在 status_monitor.py 中（此处按需惰性导入，避免循环依赖）。
"""
import os
import re
import sys
import json
import math
import time
import html as _html
import tempfile
import threading
import urllib.request
import webbrowser

import data_store

from PyQt5.QtCore import (Qt, QTimer, QPoint, QPointF, QRect, QRectF, QSize,
                          QObject, QEvent, QUrl, pyqtSignal)
from PyQt5.QtGui import (QFont, QFontMetrics, QPainter, QPainterPath, QColor,
                         QPen, QPixmap, QTextOption)
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                             QTextBrowser, QTextEdit, QScrollBar, QMenu,
                             QVBoxLayout, QHBoxLayout, QGridLayout)


def _scaled_qss(qss):
    """气泡内控件 QSS 按气泡档位整体放大（根因统一：font/padding/圆角一起变）。"""
    try:
        from widgets import kit as _kit
        return _kit.scale_qss(qss)
    except Exception:
        return qss


def _kit_scale():
    """当前气泡档位（bubble_scale），供富文本字号/行高整体缩放。"""
    try:
        from widgets import kit as _kit
        return _kit.bubble_k()
    except Exception:
        return 1.0


# ==================== 动画统一口径 ====================
# 气泡里所有开合/补位动画共用这一套，保证"风格一致、全程顺滑"：
# - 一律 60fps（16ms）。折叠动画原来用 24ms，0.22s 只出 11 帧、单帧跳 37px，
#   就是用户说的"一跳一跳"。
# - 缓动一律 ease_out_cubic：起步最快、越接近终点越慢，收尾自然停住，不过冲。
#   （smoothstep 头尾都慢，中段反而更陡，帧少时跳得更明显。）
ANIM_MS = 16
ANIM_FOLD_DUR = 0.26        # 对话面板收起/展开（还会按距离拉长，见 _fold_dur）
ANIM_OPEN_DUR = 0.18        # 气泡整体弹出/收回
ANIM_SETTLE_DUR = 0.16      # 内容变化后的补位


def ease_out(k):
    """头快尾慢，终点导数为 0：适合淡入淡出这类"出现/消失"。"""
    k = 0.0 if k < 0.0 else (1.0 if k > 1.0 else k)
    return 1.0 - (1.0 - k) ** 3


def ease_in_out(k):
    """两头都慢、中段匀速（smoothstep）：**尺寸和位置变化一律用这个**。

    ease_out 起步最快，折叠 224px 时第一帧就要跳 48px，看着像"咔"一下；
    两头慢的曲线把位移摊开，起步和收尾都贴着 0 速度，才是"流畅矢量"的观感。
    """
    k = 0.0 if k < 0.0 else (1.0 if k > 1.0 else k)
    return k * k * (3.0 - 2.0 * k)


def _dist_dur(dist, base=ANIM_FOLD_DUR, lo=0.18, hi=0.40):
    """按位移距离拉长时长：距离大就多给点时间，单帧步长才不会失控。

    固定时长的话，收起 500px 的面板和收起 80px 的用一样的时间，前者每帧要跳
    好几倍——同一套动画看起来快慢不一。
    """
    d = abs(float(dist))
    return max(lo, min(hi, base * (d / 220.0) ** 0.5)) if d > 1 else lo


def _bs(v):
    """气泡内逻辑像素按气泡档位缩放（同 widgets.kit.bs）。"""
    try:
        from widgets import kit as _kit
        return _kit.bs(v)
    except Exception:
        return max(1, int(round(v)))


def _base_font_pt():
    """气泡基础字号（点），随档位缩放。"""
    return 7.5 * _kit_scale()


def _st_monitor(name):
    """惰性获取 status_monitor 中的符号（避免模块级循环导入）。"""
    import status_monitor as _sm
    return getattr(_sm, name)


class _BubbleTip(QWidget):
    """气泡内按钮的悬停介绍标签：风格与桌宠的 _TipLabel 统一。"""

    def __init__(self, parent=None):
        # 传入 parent 以建立 QObject 归属：即便带 Qt.ToolTip 仍是独立顶层窗口，
        # 但会随 parent（ChatPanel）一起销毁，避免每次重建泄漏一个提示窗+定时器。
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._text = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def _font(self):
        f = QFont("Microsoft YaHei")
        f.setPointSizeF(8.0 * _kit_scale())
        return f

    def show_text(self, text, gpos):
        if text != self._text:
            self._text = text
            try:
                fm = QFontMetrics(self._font())
                self.setFixedSize(fm.horizontalAdvance(text) + _bs(12), fm.height() + _bs(6))
            except Exception:
                self.resize(_bs(84), _bs(22))
        self.move(gpos.x() - self.width() // 2, gpos.y() - self.height() - _bs(8))
        self.show()
        self.raise_()
        self._hide_timer.start(1500)   # 1.5 秒后自动消失

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(255, 255, 255, 235))
        p.setPen(QPen(QColor(187, 187, 187, 220), 1))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, _bs(6), _bs(6))
        p.setPen(QColor(51, 51, 51))
        p.setFont(self._font())
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()


class _TrashIconButton(QPushButton):
    """常驻小按钮：QPainter 绘制"带斜线的方框"图案（小尺寸）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_bs(14), _bs(12))
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(200, 215, 240, 200)
        w, h = self.width(), self.height()
        s = _bs(8)                               # 图案尺寸（随档位）
        x0 = (w - s) // 2
        y0 = (h - s) // 2
        p.setPen(QPen(c, 1))
        p.drawRect(x0, y0, s - 1, s - 1)         # 方框
        p.drawLine(x0, y0 + s - 1, x0 + s - 1, y0)  # 对角斜线
        p.end()


class _ArrowButton(QPushButton):
    """上下箭头小按钮：QPainter 绘制上/下箭头。"""

    def __init__(self, direction, parent=None):
        super().__init__(parent)
        self.setFixedSize(_bs(14), _bs(12))
        self.setCursor(Qt.PointingHandCursor)
        self._dir = "up" if direction == "up" else "down"

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(200, 215, 240, 200)
        if not self.isEnabled():
            c = QColor(200, 215, 240, 55)   # 禁用时箭头变暗
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        s = 4.0 * _kit_scale()
        pen = QPen(c, 1)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        if self._dir == "up":
            p.drawLine(QPointF(cx - s, cy + s / 2), QPointF(cx, cy - s / 2))
            p.drawLine(QPointF(cx, cy - s / 2), QPointF(cx + s, cy + s / 2))
        else:
            p.drawLine(QPointF(cx - s, cy - s / 2), QPointF(cx, cy + s / 2))
            p.drawLine(QPointF(cx, cy + s / 2), QPointF(cx + s, cy - s / 2))
        p.end()


class _FoldButton(QPushButton):
    """收纳按钮：展开态实心向下三角，收起态实心向上三角。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_bs(14), _bs(12))
        self._down = True

    def set_down(self, down):
        self._down = bool(down)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(200, 215, 240, 200)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        s = 4.0 * _kit_scale()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        path = QPainterPath()
        if self._down:
            path.moveTo(cx - s, cy - s / 2)
            path.lineTo(cx + s, cy - s / 2)
            path.lineTo(cx, cy + s / 2)
        else:
            path.moveTo(cx - s, cy + s / 2)
            path.lineTo(cx + s, cy + s / 2)
            path.lineTo(cx, cy - s / 2)
        path.closeSubpath()
        p.drawPath(path)
        p.end()


class _ThumbStrip(QWidget):
    """待发送图片的迷你缩略图条：多张并列且部分重叠（堆叠展示）。"""

    _TW = 24       # 单张缩略图宽（逻辑值，随档位缩放）
    _TH = 24       # 单张缩略图高
    _OVERLAP = 8   # 相邻图片重叠像素

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = []
        self.setFixedHeight(_bs(self._TH))
        self.setMinimumWidth(0)
        self.hide()

    def set_images(self, paths):
        tw, th, ov = _bs(self._TW), _bs(self._TH), _bs(self._OVERLAP)
        pms = []
        for p in paths:
            pm = QPixmap(p)
            if not pm.isNull():
                pms.append(pm.scaled(tw, th,
                                     Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._pixmaps = pms[:8]   # 最多显示 8 张，防止附件条被撑满
        if self._pixmaps:
            w = tw + max(0, len(self._pixmaps) - 1) * (tw - ov)
            self.setFixedWidth(min(_bs(160), w))
            self.show()
        else:
            self.setFixedWidth(0)
            self.hide()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        th, ov = _bs(self._TH), _bs(self._OVERLAP)
        x = 0
        for pm in self._pixmaps:
            y = (th - pm.height()) // 2
            p.drawPixmap(x, y, pm)
            p.setPen(QPen(QColor(255, 255, 255, 140), 1))
            p.drawRect(x, y, pm.width() - 1, pm.height() - 1)
            x += _bs(self._TW) - ov
        p.end()


class _ChatBtnFilter(QObject):
    """收纳按钮/垃圾桶按钮的悬停联动。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Enter, QEvent.Leave):
            self._panel._on_btn_hover()
        return False


class _ChatInput(QTextEdit):
    """多行聊天输入框：默认一行，Shift+Enter 换行（最多两行），Enter 发送。

    发送/贴图都走信号，**不要再用 `parentWidget()` 去找面板**：输入行现在包在
    一层 QWidget 里（见 ChatPanel 的 `_bottom`），加进那层的布局时 Qt 会把父
    控件改成那层 QWidget，`parentWidget()._send()` 就成了 AttributeError——
    表现就是"打完字回车发不出去"。信号连在谁身上和控件树多深没关系。
    """

    submitted = pyqtSignal()          # Enter：发送当前输入
    image_pasted = pyqtSignal(str)    # 粘贴图片：本地临时文件路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().contentsChanged.connect(self._auto_height)
        self._auto_height()

    def _auto_height(self):
        """按文档**真实排版高度**长高（最多两行）。

        不能只数 `blockCount()`：Shift+Enter 插的是软换行、还有自动折行，
        两者都不增加段落数——那样第二行会被裁在框外，看着像字打丢了。
        """
        doc = self.document()
        try:
            txt_h = doc.documentLayout().documentSize().height()
        except Exception:
            txt_h = _bs(17) * max(1, doc.blockCount())
        h = min(_bs(44), max(_bs(26), int(round(txt_h)) + _bs(8)))
        self.setFixedHeight(h)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """粘贴图片：保存为本地文件并交给面板；粘贴富文本：只取纯文本（去掉大字体/底色）。"""
        try:
            if source.hasImage():
                img = source.imageData()
                pm = QPixmap.fromImage(img) if hasattr(img, "save") else img
                tmp = os.path.join(tempfile.gettempdir(),
                                   "oi_paste_%d.png" % int(time.time() * 1000))
                if pm.save(tmp, "PNG"):
                    self.image_pasted.emit(tmp)
                    return
        except Exception:
            pass
        # 网页/富文本粘贴：丢弃格式，只插入纯文本，避免带入不合时宜的字体与底色
        if source.hasText():
            self.insertPlainText(source.text())
            return
        super().insertFromMimeData(source)


class _ChatHistory(QTextBrowser):
    """对话历史框：拦截自定义 scheme 链接点击，避免 Qt 导航把文档清空。"""

    _MENU_QSS = (
        "QMenu{background:#232a3a;border:1px solid #4a5468;border-radius:6px;"
        "padding:3px;font-family:'Microsoft YaHei';font-size:11px;color:#d5dbe8;}"
        "QMenu::item{padding:4px 18px;border-radius:3px;margin:0 1px;}"
        "QMenu::item:selected{background:#4a90e2;color:#ffffff;}"
        "QMenu::item:disabled{color:#566070;background:transparent;}"
    )

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def setSource(self, url):
        """拦截一切链接跳转（复制/外部/审批），防止 Qt 默认导航清空文档。"""
        try:
            self._panel._on_history_link(QUrl(url))
        except Exception:
            pass
        return True

    def contextMenuEvent(self, event):
        """右键：按消息提供中文小菜单（引用/复制），不再弹出大而杂的默认菜单。"""
        try:
            event.accept()   # 先消费事件，防止默认菜单叠加出现"重影"
            idx = self._panel._msg_index_at(event.pos())
            link = self.anchorAt(event.pos())
            menu = QMenu(self)
            menu.setStyleSheet(_scaled_qss(self._MENU_QSS))
            act_ref = menu.addAction("引用")
            act_ref.setEnabled(idx >= 0)   # 未命中消息时置灰
            if idx >= 0:
                act_ref.triggered.connect(
                    lambda _=False, i=idx: self._panel._set_quoted(i))
            if link and link.startswith("http"):
                # 鼠标在链接上：识别并允许复制链接地址
                act_link = menu.addAction("复制链接")
                act_link.triggered.connect(
                    lambda _=False, u=link: self._panel._copy_link(u))
            act_copy = menu.addAction("复制")
            act_copy.setEnabled(self.textCursor().hasSelection())  # 未选中文字时置灰
            act_copy.triggered.connect(self.copy)
            bubble = getattr(self._panel, "_bubble", None)
            if bubble is not None:
                with _menu_hold(bubble):
                    menu.exec_(event.globalPos())
            else:
                menu.exec_(event.globalPos())
        except Exception:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            url = self.anchorAt(event.pos())
            if url:
                from PyQt5.QtCore import QUrl
                self._panel._on_history_link(QUrl(url))
                event.accept()
                return
        super().mouseReleaseEvent(event)


class ChatPanel(QWidget):
    """聊天面板：身份配色记录、图片缩略图、最多 50 条、10 行滚动、折叠、思考动画"""

    _MSG_MAX = 50
    _BASE_H = 54          # 输入区基准高度（含 4 个右侧小按钮列）
    _image_ready = pyqtSignal(object, object)   # (消息, 本地图片路径)：后台下载完成后投递到主线程
    _HIST_QSS = (
        "QTextBrowser{background:rgba(255,255,255,18);border:none;color:#e8ecf5;"
        "padding:1px 2px;}"
        "QScrollBar:vertical{background:transparent;width:4px;margin:0;}"
        "QScrollBar::handle:vertical{background:rgba(255,255,255,75);"
        "border-radius:2px;min-height:16px;}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
    )
    _FOLD_QSS = ("QPushButton{border:none;background:transparent;color:rgba(200,215,240,180);"
                  "font-size:10px;padding:0;}"
                  "QPushButton:hover{color:#ffffff;background:rgba(255,255,255,35);border-radius:3px;}"
                  "QPushButton:disabled{color:rgba(200,215,240,55);background:transparent;}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._BASE_H = _bs(54)   # 输入区基准高度随气泡档位缩放（覆盖类常量）
        self._fold_base = self._BASE_H   # 折叠动画期间恒定的"历史区以外"高度
        self.on_send = None
        self.on_stop = None
        self.on_resize = None
        self._provider = None
        self._messages = []       # 当前对话消息（单会话；保存时仍写多会话兼容格式）
        self._nav_idx = -1        # 消息浏览锚点（当前停在视口顶部的消息下标）
        self._nav_active = False  # 是否正在用上/下按钮逐条浏览
        self._nav_lock = False    # 程序化滚动时抑制 valueChanged 重置锚点
        self._fold_anim = None    # 收起/展开动画 {"t0","dur","h0","h1"}：只动对话面板本身
        self._fold_anchor = 0     # 折叠动画期间的锚点（屏幕坐标）
        self._fold_mode = "bottom"  # "bottom"=底边锚定（顶部下移）/"top"=顶边锚定（底部上抬）
        self._fold_x = 0          # 折叠动画期间的气泡中心 x
        self._code_blocks = []    # 本轮渲染中的代码块（供"复制"链接索引）
        self._expanded_code = set()  # 已展开的长代码块索引（点击"展开"后记录）
        self._preserve_scroll = False   # 展开/收起代码块时保持当前滚动位置
        self._preserve_scroll_val = 0
        self._collapsed = False
        self._thinking = False
        self._greeting = ""
        self._dots = 0
        self._streaming = False    # 流式回复进行中
        self._stream_text = ""     # 当前流式回复的累计文本
        self._stream_html = ""     # 当前流式回复的原始 HTML（webchat 网页会话用）
        self._header_widget = None # 顶部标题栏（webchat 模块用，可空）
        self._status_text = ""     # 连接/接收状态（连接中…/接收中…）
        self._thinking_text = ""   # 思考过程（reasoning_content）
        self._quoted = None        # 引用的消息 {"role":..., "text":...}
        self._pending_images = []  # 粘贴待发送的本地图片路径
        self._dot_timer = QTimer(self)
        self._dot_timer.setTimerType(Qt.PreciseTimer)
        self._dot_timer.timeout.connect(self._tick_thinking)
        self._fold_timer = QTimer(self)
        self._fold_timer.setTimerType(Qt.PreciseTimer)
        self._fold_timer.timeout.connect(self._fold_tick)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_history)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_bs(1))
        self.history = _ChatHistory(self)
        self.history.setFrameShape(QTextBrowser.NoFrame)
        self.history.setStyleSheet(_scaled_qss(self._HIST_QSS))
        try:
            _hf = QFont("Microsoft YaHei")
            _hf.setFamilies(["Microsoft YaHei", "Segoe UI Symbol",
                             "Arial Unicode MS", "Noto Sans Symbols"])
            self.history.setFont(_hf)
        except Exception:
            pass
        self.history.setMaximumHeight(_bs(150))
        self.history.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.history.setOpenExternalLinks(False)   # 统一走 mouseReleaseEvent/setSource（复制/外部链接）
        self.history.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lay.addWidget(self.history, 1)
        # 折叠态：单行省略、上下居中显示最后一条消息
        self.collapsed_label = QLabel()
        self.collapsed_label.setStyleSheet(_scaled_qss(
            "color:#e8ecf5;font-size:10px;background:rgba(255,255,255,18);"
            "border:none;border-radius:4px;padding:1px 6px;"))
        self.collapsed_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.collapsed_label.hide()
        # 不给伸缩权：它只有一行字，拿了伸缩权就会把剩余空间全吃掉，
        # 收起态变成一条 40px 高的灰条，底下的输入行反而被挤出面板。
        # 行高统一用 kit.row_height()（已含中文下沉余量），跟标题栏一致。
        try:
            from widgets import kit as _k
            self.collapsed_label.setFixedHeight(_k.row_height())
        except Exception:
            self.collapsed_label.setFixedHeight(_bs(19))
        lay.addWidget(self.collapsed_label, 0)
        # 附件条：引用消息 / 待发送图片，右侧"发送"和"✕"
        self._quote_bar = QWidget(self)
        self._quote_bar.setStyleSheet(_scaled_qss(
            "background:rgba(255,255,255,16);border-left:2px solid #7db6ff;"
            "border-radius:3px;"))
        qb_lay = QHBoxLayout(self._quote_bar)
        qb_lay.setContentsMargins(_bs(4), _bs(1), _bs(4), _bs(1))
        qb_lay.setSpacing(_bs(4))
        self._thumb_strip = _ThumbStrip(self._quote_bar)
        qb_lay.addWidget(self._thumb_strip)
        self._quote_text = QLabel("", self._quote_bar)
        self._quote_text.setStyleSheet(_scaled_qss(
            "color:#aab3c5;font-size:10px;border:none;background:transparent;"))
        self._quote_text.setWordWrap(True)
        qb_lay.addWidget(self._quote_text, 1)
        self._send_attach_btn = QPushButton("发送", self._quote_bar)
        self._send_attach_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self._send_attach_btn.clicked.connect(self._send)
        qb_lay.addWidget(self._send_attach_btn)
        self._del_attach_btn = QPushButton("✕", self._quote_bar)
        self._del_attach_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self._del_attach_btn.clicked.connect(self._clear_attachment)
        qb_lay.addWidget(self._del_attach_btn)
        self._quote_bar.hide()
        lay.addWidget(self._quote_bar)
        # 输入行单独包一层 QWidget：它的 sizeHint 就是这一行的真实高度，
        # current_height() 直接问它，不必再手算（以前是写死的 _BASE_H=54，
        # 一旦加了标题栏、或输入框打到第二行，面板就不够高，底下那排按钮
        # 会被裁掉——用户看到的"收纳后被裁切、右侧按钮没对齐"就是这个）。
        self._bottom = QWidget(self)
        row = QHBoxLayout(self._bottom)
        row.setContentsMargins(0, 0, _bs(1), 0)   # 按钮尽量贴近右侧边框
        row.setSpacing(_bs(4))
        self.input = _ChatInput(self)
        self.input.setPlaceholderText("输入消息，Enter 发送…")
        self.input.submitted.connect(self._send)
        self.input.image_pasted.connect(self._add_pasted_image)
        self.input.setStyleSheet(_scaled_qss(
            "QTextEdit{background:rgba(255,255,255,30);"
            "border:1px solid rgba(255,255,255,60);border-radius:4px;"
            "color:#e8ecf5;padding:2px 6px;font-size:10px;}"))
        self.input.document().contentsChanged.connect(
            lambda: self.on_resize() if self.on_resize else None)
        row.addWidget(self.input, 1)
        # 2x2 按钮宫格：上/下条对话（第一行）、收纳/清屏（第二行），与输入框等高
        btn_col = QGridLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(_bs(1))
        self.up_btn = _ArrowButton("up", self)
        self.up_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self.up_btn.setCursor(Qt.PointingHandCursor)
        self.up_btn.setToolTip("上一条消息")
        self.up_btn.clicked.connect(lambda: self._nav_message(-1))
        btn_col.addWidget(self.up_btn, 0, 0)
        self.down_btn = _ArrowButton("down", self)
        self.down_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self.down_btn.setCursor(Qt.PointingHandCursor)
        self.down_btn.setToolTip("下一条消息")
        self.down_btn.clicked.connect(lambda: self._nav_message(1))
        btn_col.addWidget(self.down_btn, 0, 1)
        self.fold_btn = _FoldButton(self)
        self.fold_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self.fold_btn.setCursor(Qt.PointingHandCursor)
        self.fold_btn.clicked.connect(self.toggle_collapse)
        btn_col.addWidget(self.fold_btn, 1, 0)
        self.trash_btn = _TrashIconButton(self)
        self.trash_btn.setStyleSheet(_scaled_qss(self._FOLD_QSS))
        self.trash_btn.clicked.connect(self._clear_chat)
        btn_col.addWidget(self.trash_btn, 1, 1)
        # 按钮列贴输入框底边对齐：输入框打成多行会往上长，居中对齐的话
        # 这一列就会浮在中间，和输入框底边错开。
        row.addLayout(btn_col, 0)
        row.setAlignment(btn_col, Qt.AlignBottom)
        # 停止按钮：AI 回复/思考中可打断（类似网页输入框的停止）
        self._stop_btn = QPushButton("停止", self)
        self._stop_btn.setStyleSheet(_scaled_qss(
            "QPushButton{border:none;border-radius:4px;padding:1px 6px;"
            "font-size:10px;color:#ffe0e0;background:rgba(215,80,80,150);}"
            "QPushButton:hover{background:rgba(235,105,105,200);}"
            "QPushButton:pressed{background:rgba(190,70,70,190);}"))
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setToolTip("停止生成")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.hide()
        row.addWidget(self._stop_btn)
        row.setAlignment(self._stop_btn, Qt.AlignBottom)
        lay.addWidget(self._bottom, 0)
        self.trash_btn.show()
        self._tip = _BubbleTip(self)
        self._btn_filter = _ChatBtnFilter(self)
        self.fold_btn.installEventFilter(self._btn_filter)
        self.trash_btn.installEventFilter(self._btn_filter)
        self.up_btn.installEventFilter(self._btn_filter)
        self.down_btn.installEventFilter(self._btn_filter)
        self._image_ready.connect(self._apply_image)
        self.history.verticalScrollBar().valueChanged.connect(self._on_history_scrolled)
        self._update_nav_btns()
        self.hide()

    def _on_btn_hover(self):
        over_up = self.up_btn.underMouse()
        over_down = self.down_btn.underMouse()
        over_fold = self.fold_btn.underMouse()
        over_trash = self.trash_btn.isVisible() and self.trash_btn.underMouse()
        if over_up:
            self._show_tip("上一条消息", self.up_btn)
        elif over_down:
            self._show_tip("下一条消息", self.down_btn)
        elif over_fold:
            self._show_tip("折叠/展开对话记录", self.fold_btn)
        elif over_trash:
            self._show_tip("清空对话记录，保留问候语", self.trash_btn)
        else:
            self._hide_tip()

    def _show_tip(self, text, btn):
        try:
            g = btn.mapToGlobal(QPoint(btn.width() // 2, 0))
            self._tip.show_text(text, g)
        except Exception:
            pass

    def _hide_tip(self):
        try:
            self._tip._hide_timer.stop()
            self._tip.hide()
        except Exception:
            pass

    def _on_history_link(self, url):
        """历史框链接：copy: 复制代码块；其余链接交给系统浏览器。"""
        try:
            s = url.toString()
            if not s or s.startswith("about:"):
                return
            if s.startswith("copy:"):
                idx = int(s[len("copy:"):])
                blocks = self._code_blocks or _chat_html._blocks
                if 0 <= idx < len(blocks):
                    from PyQt5.QtWidgets import QApplication
                    QApplication.clipboard().setText(blocks[idx])
                    g = self.history.mapToGlobal(QPoint(self.history.width() // 2, 8))
                    self._tip.show_text("代码已复制", g)
                return
            if s.startswith("expandcode:"):
                # 展开被折叠的长代码块
                try:
                    self._expanded_code.add(int(s[len("expandcode:"):]))
                except Exception:
                    pass
                self._preserve_scroll = True
                self._preserve_scroll_val = self.history.verticalScrollBar().value()
                self.rebuild()
                return
            if s.startswith("collapsecode:"):
                # 收起已展开的代码块
                try:
                    self._expanded_code.discard(int(s[len("collapsecode:"):]))
                except Exception:
                    pass
                self._preserve_scroll = True
                self._preserve_scroll_val = self.history.verticalScrollBar().value()
                self.rebuild()
                return
            if s == "quoteclose://":
                self._clear_attachment()
                return
            if s == "thinktoggle://":
                self._thinking_text = ""
                self.rebuild()
                return
            import webbrowser
            webbrowser.open(s)
        except Exception:
            pass

    def _copy_link(self, url):
        """把链接地址复制到剪贴板，并给出提示。"""
        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setText(url)
            self._show_tip("链接已复制", self.history)
        except Exception:
            pass

    def _on_stop_clicked(self):
        """停止按钮：通知气泡中断当前回复（llm 中止流式 / webchat 停止轮询）。"""
        try:
            self._stop_btn.hide()
        except Exception:
            pass
        if self.on_stop:
            try:
                self.on_stop()
            except Exception:
                pass

    def _update_stop_btn(self):
        """回复/思考中显示停止按钮，结束/清空时隐藏。"""
        try:
            self._stop_btn.setVisible(bool(self._streaming or self._thinking))
        except Exception:
            pass

    def _clear_chat(self):
        """清空所有会话，只保留一句默认问候语。"""
        # 清空是不可撤销的（连持久化的历史一起没），有内容时先问一句。
        # 这个按钮就挨着折叠键，误点过一次记录就全丢了。
        if self._messages:
            try:
                from widgets import kit as _k
                if not _k.confirm(self, "清空对话",
                                  "要清空这个会话的 %d 条记录吗？\n清完只留一句问候语，"
                                  "不能撤销。" % len(self._messages), danger=True):
                    return
            except Exception:
                pass
        self._cancel_fold()
        self._stop_thinking()
        self._thinking_text = ""
        self._streaming = False
        self._stream_text = ""
        self._clear_attachment()
        if self._provider is not None:
            try:
                self._provider.clear_summary()   # 清空对话同时清掉压缩摘要
            except Exception:
                pass
        self._messages = []
        self._nav_idx = -1
        self._nav_active = False
        if self._greeting:
            self.add_message("assistant", self._greeting)
        else:
            self.rebuild()
            if self.on_resize:
                self.on_resize()
        self._update_nav_btns()
        self._save_timer.start(200)
        self._hide_tip()

    def _calc_hist_h(self):
        """按消息行数估算历史区高度（一行消息=一行高度，最多 150px）。"""
        if not self._messages:
            return 0
        lines = 0
        try:
            pw = self.parentWidget().width() if self.parentWidget() else self.width()
            avail = max(_bs(60), pw - _bs(32))  # 消息可用宽度（扣除边距与滚动条）
            f = QFont("Microsoft YaHei")
            f.setPointSizeF(7.5 * _kit_scale())
            fm = QFontMetrics(f)
            label_w = fm.horizontalAdvance("AI：")
            for m in self._messages:
                if m.get("image"):
                    lines += 3                 # 图片缩略图约占 3 行
                w = fm.horizontalAdvance(str(m.get("text", ""))) + label_w
                lines += max(1, math.ceil(w / avail))
        except Exception:
            lines = 3
        return min(_bs(150), max(1, min(10, lines)) * _bs(16))

    def _base_h(self):
        """历史区**以外**的所有固定高度：标题栏 + 收起态单行 + 引用条 + 输入行。

        以前这里是写死的 `_BASE_H = 54`。AI 助手面板带标题栏（22px），收起态
        还要再加一行摘要，54 根本不够——底下那排折叠/清屏按钮就被裁在面板外，
        看起来像"收纳后没做适配、按钮没对齐"。改成问真实的 sizeHint，加了什么
        部件都不会再算漏。
        """
        try:
            lay = self.layout()
            m = lay.contentsMargins()
            h = m.top() + m.bottom()

            def _ph(w):
                # setFixedHeight() 不改 sizeHint，只改 min/max——标题栏就是这么
                # 被算少 7px 的。两者取大才是它真正要占的高度。
                # 但也**必须夹上 maximumHeight**：折叠动画会把摘要行
                # setFixedHeight(0)，而它的 sizeHint 仍是一行字的高度，不夹的话
                # 这里会多算一行（实测展开末帧多出 24px）。
                h = max(w.sizeHint().height(), w.minimumHeight())
                return min(h, w.maximumHeight())

            parts = [_ph(self._bottom)]
            if self._header_widget is not None \
                    and self._header_widget.isVisibleTo(self):
                parts.append(_ph(self._header_widget))
            if self._quote_bar.isVisibleTo(self):
                parts.append(_ph(self._quote_bar))
            if self.collapsed_label.isVisibleTo(self):
                # 按**可见性**而不是 self._collapsed 来算：折叠动画期间这一行的
                # 高度是从 0 长到满（或反过来）的，必须如实计入，否则内容高度和
                # 外框高度对不上，多出来的空间会被布局塞给输入区（输入框被拉伸）。
                parts.append(_ph(self.collapsed_label))
            # 可见部件之间各有一道间距；历史区那一道由调用方补
            return h + sum(parts) + lay.spacing() * max(0, len(parts) - 1)
        except Exception:
            return self._BASE_H

    def current_height(self):
        """高度随消息行数自适应：一行消息=一行高度，最多 10 行；无消息折叠。"""
        base = self._base_h()
        sp = self.layout().spacing()
        if self._streaming:
            # 流式期间预留最大高度，保持气泡尺寸稳定（输入区 + 150 历史区）
            return base + sp + _bs(150)
        if self._fold_anim is not None:
            # 折叠动画期间：返回动画中的外框高度。与 _fold_tick 用同一条曲线、
            # 同一对端点，保证"面板自报的高度"和"实际摆出来的几何"始终一致。
            a = self._fold_anim
            k = min(1.0, (time.monotonic() - a["t0"]) / max(0.01, a["dur"]))
            return int(round(self._fold_total(a, ease_in_out(k))))
        if not self._messages:
            return 0
        if self._collapsed:
            return base
        return self._calc_hist_h() + sp + base

    def toggle_collapse(self):
        """收起/展开：历史区内容从上往下滑动折叠（底部固定、顶部下移），
        外框高度每帧同步往下收/向上抬，全程平滑，不做瞬间切尺寸。"""
        if self._fold_anim is not None:
            return   # 动画进行中忽略重复点击
        # 改动任何可见性之前先记下真实外框高度：动画必须从这个值起步，
        # 否则第一帧会直接跳到"终局基座"上（实测收起 +47px、展开 -22px）
        total0 = float(self.current_height())
        if self._collapsed:
            # 展开：模块外框逐帧变高，窗口随模块同步顶长（锚点固定）
            self._collapsed = False
            self.fold_btn.set_down(True)    # 展开态：向下三角（点击收起）
            # 摘要行**不在这里 hide**：它的高度由动画从满收到 0（见
            # _start_hist_anim），收完了才在 _finish_fold 里真正藏掉。
            self.history.show()
            self.history.setMaximumHeight(_bs(150))
            self.history.setFixedHeight(0)
            self.layout().setAlignment(self.history, Qt.AlignBottom)
            self._capture_fold_anchor()
            self._lock_bubble(True)
            self._start_hist_anim(0.0, float(self._calc_hist_h()), total0)
        else:
            # 收起：内容与外框下滑收拢（窗口不动），结束后平滑收气泡
            self._collapsed = True
            self.fold_btn.set_down(False)   # 收起态：向上三角（点击展开）
            # 摘要行从 0 长到满，和消息区的收拢同步。以前它是等 _finish_fold
            # 才出现的：整段动画里那一行的空位没人占，QVBoxLayout 就把它分给了
            # 可伸缩的输入区——"消息栏在收，输入框却一直是拉伸的，最后一刻才
            # 弹回正常高度"，看着就是卡顿。
            try:
                self._update_collapsed_label()
            except Exception:
                pass
            self.collapsed_label.setFixedHeight(0)
            self.collapsed_label.show()
            self.layout().setAlignment(self.history, Qt.AlignBottom)
            self._capture_fold_anchor()
            self._lock_bubble(True)
            self._start_hist_anim(float(self._calc_hist_h()), 0.0, total0)

    def _collapsed_label_full_h(self):
        """收起态摘要行摆满时该多高（动画的一个端点）。"""
        from widgets import kit as _k
        try:
            return int(_k.row_height())
        except Exception:
            return _bs(19)

    def _start_hist_anim(self, h0, h1, total0=None):
        """历史区 h0->h1 的折叠动画。

        三个量一起插值，保证**每一帧内容高度都等于外框高度**：
          - 历史区   h0 -> h1
          - 摘要行   收起时 0 -> 满，展开时 满 -> 0
          - 外框     点击前的真实高度 -> 终局真实高度
        少了摘要行这一路，两端基座不一样（收起态多一行），中途的差额就会被布局
        分给可伸缩的输入区，于是输入框在动画期间被拉伸/压扁，最后一刻才归位。
        `total0` 是点击前的真实外框高度，首帧从它起步，不会先跳一下。
        """
        sp = self.layout().spacing()
        lab = self.collapsed_label
        full = self._collapsed_label_full_h()
        lab0 = float(lab.height()) if lab.isVisibleTo(self) else 0.0
        lab1 = float(full) if self._collapsed else 0.0
        # 终局外框：把摘要行先摆成终局高度，问一次真实基座，再摆回来
        lab.setFixedHeight(int(round(lab1)))
        total1 = float(self._base_h()) + ((sp + float(h1)) if h1 > 0 else 0.0)
        if lab1 <= 0:
            # 终局会把摘要行 hide 掉，它在布局里那一道间距也就不存在了。
            # 动画期间它一直是 visible（高度收到 0），_base_h 会替它算一道。
            total1 -= sp
        lab.setFixedHeight(int(round(lab0)))
        if total0 is None:
            total0 = float(self._base_h()) + ((sp + float(h0)) if h0 > 0 else 0.0)
        self._fold_anim = {"t0": time.monotonic(),
                           "dur": _dist_dur(total1 - total0),
                           "h0": h0, "h1": h1,
                           "lab0": lab0, "lab1": lab1,
                           "tot0": float(total0), "tot1": total1}
        self._fold_timer.start(ANIM_MS)
        self._fold_tick()

    def _fold_total(self, a, e):
        """动画进行到缓动值 e 时的外框高度。"""
        return a["tot0"] + (a["tot1"] - a["tot0"]) * e

    def _fold_tick(self):
        """动画每帧：同步历史区高度与模块外框/气泡窗口高度（锚点固定），全程平滑。"""
        try:
            a = self._fold_anim
            if a is None:
                return
            k = min(1.0, (time.monotonic() - a["t0"]) / max(0.01, a["dur"]))
            e = ease_in_out(k)            # 尺寸变化统一用两头慢的缓动
            h = a["h0"] + (a["h1"] - a["h0"]) * e
            lab = a["lab0"] + (a["lab1"] - a["lab0"]) * e
            self.collapsed_label.setFixedHeight(max(0, int(round(lab))))
            self.history.setFixedHeight(max(0, int(h)))
            # 模块外框（当前模块区域）同步收放；窗口本身不缩放，避免重影
            self._bubble_fold_height(int(round(self._fold_total(a, e))))
            if k >= 1.0:
                self._fold_timer.stop()
                self._fold_anim = None
                self._finish_fold()
        except Exception:
            self._fold_timer.stop()
            self._fold_anim = None
            self._finish_fold()

    def _finish_fold(self):
        """动画结束：恢复正常布局并让外层做最后一次尺寸同步。"""
        try:
            self.layout().setAlignment(self.history, Qt.Alignment())   # 恢复默认填充
            self.history.setMaximumHeight(_bs(16) if self._collapsed else _bs(150))
            self.history.setFixedHeight(16777215)   # 解除固定，交给外层布局
            # 摘要行的高度在动画里被逐帧改过，收尾要还原成正常的一行高
            self.collapsed_label.setFixedHeight(self._collapsed_label_full_h())
            if self._collapsed:
                self.collapsed_label.show()
            else:
                self.collapsed_label.hide()
            self.rebuild()   # 收起态换单行标签 / 展开态显示历史
            self._lock_bubble(False)
        except Exception:
            pass

    def _capture_fold_anchor(self):
        """折叠开始前：保持气泡当前所在位置/侧别，锚定当前侧的外边缘。
        收起/展开只在原位收放，绝不跳到另一侧计算再弹回。"""
        try:
            b = getattr(self, "_bubble", None)
            if b is None:
                return
            self._fold_x = b.x()
            side = b._current_side()
            if side == "above":
                # 桌宠上方：底边锚定，顶部下移
                self._fold_anchor = b.y() + b.height()
                self._fold_mode = "bottom"
            elif side == "below":
                # 桌宠下方：顶边锚定，底部上抬
                self._fold_anchor = b.y()
                self._fold_mode = "top"
            else:
                # 左右侧：底边锚定（保持原位）
                self._fold_anchor = b.y() + b.height()
                self._fold_mode = "bottom"
            # 暂停位置缓动，避免与折叠几何互相拉扯
            try:
                b._mv_timer.stop()
            except Exception:
                pass
        except Exception:
            try:
                b = getattr(self, "_bubble", None)
                if b is not None:
                    self._fold_anchor = b.y() + b.height()
                    self._fold_mode = "bottom"
                    self._fold_x = b.x()
            except Exception:
                self._fold_anchor = 0
                self._fold_mode = "bottom"

    def _bubble_fold_height(self, wh):
        """把当前对话外框高度同步给气泡（只改对话行 + 气泡尺寸）。"""
        try:
            b = getattr(self, "_bubble", None)
            if b is not None and hasattr(b, "_fold_set_height"):
                b._fold_set_height(self, wh)
        except Exception:
            pass

    def _lock_bubble(self, locked):
        """折叠动画期间锁定气泡：禁止刷新重排/定位，避免整面板闪烁跳动。"""
        try:
            b = getattr(self, "_bubble", None)
            if b is not None:
                b._fold_locked = locked
        except Exception:
            pass

    def _cancel_fold(self):
        self._fold_timer.stop()
        self._fold_anim = None
        try:
            self.layout().setAlignment(self.history, Qt.Alignment())   # 恢复默认填充
            self.history.setFixedHeight(16777215)
        except Exception:
            pass
        self._lock_bubble(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._collapsed and self._messages:
            self._update_collapsed_label()

    def _update_collapsed_label(self):
        """收起态的那一行摘要：谁说的 + 内容，按标签的真实可用宽度省略。"""
        idx = self._collapsed_idx()
        last = self._messages[idx]
        txt = str(last.get("text", ""))
        if last.get("image"):
            txt += " ［图片］"
        txt = ("我：" if last.get("role") == "user" else "AI：") + txt.strip()
        n = len(self._messages)
        if n > 1:
            # 收起态也能用上/下按钮翻消息，标出看的是第几条
            txt = "%d/%d  %s" % (idx + 1, n, txt)
        f = QFont("Microsoft YaHei")
        f.setPointSizeF(7.5 * _kit_scale())
        self.collapsed_label.setFont(f)
        fm = QFontMetrics(f)
        # 用标签自己的内容宽度，不要拿面板宽减一个写死的 24：
        # 气泡放大档位下那个常数会偏小，文字被提前截断
        avail = self.collapsed_label.contentsRect().width()
        if avail <= 0:
            avail = max(20, self.width() - _bs(16))
        self.collapsed_label.setText(fm.elidedText(txt, Qt.ElideRight,
                                                   max(20, avail)))
        self.collapsed_label.setToolTip(txt)

    def _collapsed_idx(self):
        """收起态正在显示第几条消息（上/下按钮翻过的位置，默认最后一条）。"""
        if self._nav_active and 0 <= self._nav_idx < len(self._messages):
            return self._nav_idx
        return len(self._messages) - 1

    def set_header(self, title, url=None, right_text=None, open_cb=None):
        """给面板顶部加一行标题栏。

        - url 非空：右侧"进入网页"按钮；open_cb 提供时用 open_cb（如打开
          WebView2 完整窗口接续会话），否则默认浏览器打开
        - url 为空、right_text 非空：右侧显示小字说明（如 llm 模块的模型名）
        - 两者都空：只有标题
        """
        try:
            import webbrowser
            if self._header_widget is not None:
                self._header_widget.deleteLater()
            self._header_widget = QWidget(self)
            h = QHBoxLayout(self._header_widget)
            h.setContentsMargins(_bs(4), 0, _bs(4), 0)
            h.setSpacing(_bs(4))
            # 标题栏高度按统一行高（kit.row_height 已含 CJK 行距余量）：
            # 原先只给 2px/1px 内边距、标签无最小高度，中文标题底部会被裁掉一点。
            try:
                from widgets import kit as _k
                self._header_widget.setFixedHeight(_k.row_height())
            except Exception:
                pass
            t = QLabel(str(title or "网页聊天"), self._header_widget)
            t.setStyleSheet(_scaled_qss(
                "color:#a8e6a3;font-size:10px;font-weight:600;"
                "background:transparent;border:none;"))
            t.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            h.addWidget(t, 1)
            if url:
                b = QPushButton("进入网页", self._header_widget)
                b.setStyleSheet(_scaled_qss(
                    "QPushButton{border:none;border-radius:4px;padding:1px 8px;"
                    "font-size:10px;color:#e8ecf5;background:rgba(255,255,255,35);}"
                    "QPushButton:hover{background:rgba(74,144,226,140);}"))
                b.setCursor(Qt.PointingHandCursor)
                if open_cb:
                    b.clicked.connect(open_cb)
                else:
                    b.clicked.connect(lambda: webbrowser.open(url or ""))
                h.addWidget(b)
            elif right_text:
                m = QLabel(str(right_text), self._header_widget)
                m.setStyleSheet(_scaled_qss(
                    "color:#8fa3c0;font-size:10px;background:transparent;"
                    "border:none;"))
                m.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                h.addWidget(m)
            lay = self.layout()
            lay.insertWidget(0, self._header_widget)
            if self.on_resize:
                self.on_resize()
        except Exception:
            pass

    def set_provider(self, provider):
        self._cancel_fold()
        self._provider = provider
        greeting = (provider.rule or {}).get("greeting") if provider else None
        self._greeting = greeting or ""
        saved = self._load_history()
        if saved:
            self._messages = saved
            self._update_nav_btns()
            self.rebuild()
            if self.on_resize:
                self.on_resize()
            return
        self._messages = []
        self._update_nav_btns()
        if greeting:
            self.add_message("assistant", greeting)
        self.rebuild()

    def showEvent(self, event):
        """打开/显示面板时定位到最后一条消息，不用手动往下翻。"""
        super().showEvent(event)
        QTimer.singleShot(0, self._scroll_to_last)

    def _scroll_to_last(self):
        try:
            self._nav_active = False
            self._nav_idx = -1
            sb = self.history.verticalScrollBar()
            self._nav_lock = True
            try:
                sb.setValue(sb.maximum())
            finally:
                self._nav_lock = False
            self._update_nav_btns()
        except Exception:
            pass

    def _nav_message(self, delta):
        """上/下一条消息：把上/下一条消息滚到历史框顶部，方便逐条阅览。

        收起态历史框是藏着的，滚它没有意义——那时改成翻摘要行，
        否则这两个按钮在收起态是点了没反应的死键。
        """
        try:
            if not self._messages:
                return
            if self._collapsed:
                cur = self._collapsed_idx()
                target = cur + delta
                if not (0 <= target < len(self._messages)):
                    return
                self._nav_idx = target
                self._nav_active = True
                self._update_collapsed_label()
                self._update_nav_btns()
                return
            cur = self._nav_idx if (self._nav_active
                                    and 0 <= self._nav_idx < len(self._messages)) else -1
            if cur < 0:
                cur = self._current_top_msg_index()
                if cur < 0:
                    cur = len(self._messages) - 1
            target = cur + delta
            if not (0 <= target < len(self._messages)):
                return
            self._nav_idx = target
            self._nav_active = True
            self._scroll_msg_to_top(target)
            self._update_nav_btns()
        except Exception:
            pass

    def _scroll_msg_to_top(self, mi):
        """把第 mi 条消息滚到历史框顶部（上/下一条消息的定位方式）。"""
        try:
            if not (0 <= mi < len(self._msg_block_starts)):
                return
            block = self.history.document().findBlockByNumber(
                self._msg_block_starts[mi])
            if not block.isValid():
                return
            self._nav_lock = True
            try:
                y = block.layout().position().y()
                self.history.verticalScrollBar().setValue(max(0, int(y)))
            finally:
                self._nav_lock = False
        except Exception:
            pass

    def _on_history_scrolled(self, _v):
        """用户手动滚动后，上/下按钮以当前视口为准重新定位。"""
        if not self._nav_lock:
            self._nav_idx = -1
            self._nav_active = False
            self._update_nav_btns()

    def _current_top_msg_index(self):
        """视口顶部当前显示的是第几条消息（用于消息浏览定位）。"""
        try:
            cur = self.history.cursorForPosition(QPoint(2, 2))
            n = cur.block().blockNumber()
            idx = -1
            for i, s in enumerate(self._msg_block_starts):
                if s <= n:
                    idx = i
                else:
                    break
            return idx
        except Exception:
            return -1

    def _update_nav_btns(self):
        """上/下按钮按消息位置置灰：第一条禁上、最后一条禁下。"""
        try:
            n = len(self._messages)
            if self._collapsed:
                cur = self._collapsed_idx() if n else -1
            else:
                cur = self._nav_idx if (self._nav_active
                                        and 0 <= self._nav_idx < n) else -1
                if cur < 0:
                    cur = self._current_top_msg_index()
            self.up_btn.setEnabled(cur > 0)
            self.down_btn.setEnabled(0 <= cur < n - 1)
        except Exception:
            pass

    def _save_history(self):
        """把当前对话记录写入持久化文件（按规则 id 保存）。"""
        try:
            rule_id = (self._provider.rule or {}).get("id") if self._provider else None
            if not rule_id:
                return

            def _mrec(m):
                rec = {"role": m.get("role", "assistant"),
                       "text": str(m.get("text", ""))}
                if m.get("html"):
                    rec["html"] = m["html"]
                return rec

            def _mut(data):
                data[rule_id] = {"sessions": [
                    [_mrec(m) for m in self._messages[-self._MSG_MAX:]]]}
                return data
            path = _chat_history_path()
            data = data_store.read_json_path(path, {})
            _mut(data)
            data_store.write_json_path(path, data)
        except Exception:
            pass

    def _load_history(self):
        """读取该规则上次保存的对话记录。"""
        try:
            rule_id = (self._provider.rule or {}).get("id") if self._provider else None
            if not rule_id:
                return []
            data = data_store.read_json_path(_chat_history_path(), {})
            raw = data.get(rule_id) or []
            if isinstance(raw, dict) and isinstance(raw.get("sessions"), list):
                sessions = raw["sessions"]
            elif isinstance(raw, list):
                sessions = [raw]     # 旧格式：单会话
            else:
                sessions = []
            # 只保留最后一条会话（上下按钮早已改为消息浏览，不再切换会话）
            def _mrec(m):
                rec = {"role": m.get("role", "assistant"),
                       "text": str(m.get("text", "")), "image": None}
                if m.get("html"):
                    rec["html"] = m["html"]
                return rec
            return [_mrec(m) for m in sessions[-1] if m.get("text")] \
                if sessions else []
        except Exception:
            return []

    def add_message(self, role, text, image_paths=None, html=None):
        self._stop_thinking()
        text = str(text)
        paths = [p for p in (image_paths or []) if p]
        if html:
            # 原始 HTML 消息（webchat 网页会话）：直接存 HTML 渲染，不再做文本扫描
            self._messages.append({"role": role, "text": text, "html": html,
                                   "image": None, "images": paths})
            self._trim_messages()
            self.rebuild()
            if self.on_resize:
                self.on_resize()
            self._save_timer.start(400)
            return
        m = _IMG_RE.search(text)
        if m:
            # 图片链接：先占位显示，后台线程下载，不阻塞 UI
            url = m.group(0)
            text = text.replace(url, "［图片］")
            msg = {"role": role, "text": text, "image": None, "images": paths}
            self._messages.append(msg)
            self._trim_messages()
            self.rebuild()
            if self.on_resize:
                self.on_resize()
            self._fetch_image_async(msg, url)
            return
        self._messages.append({"role": role, "text": text, "image": None,
                               "images": paths})
        self._trim_messages()
        self.rebuild()
        if self.on_resize:
            self.on_resize()
        self._save_timer.start(400)

    def _trim_messages(self):
        if len(self._messages) > self._MSG_MAX:
            self._messages = self._messages[-self._MSG_MAX:]

    def add_tool_step(self, text):
        """工具执行步骤可视化：追加一行灰色步骤记录（不打断流式）。"""
        self._messages.append({"role": "tool", "text": str(text)})
        self._trim_messages()
        self.rebuild()
        if self.on_resize:
            self.on_resize()

    def _fetch_image_async(self, msg, url):
        """后台线程下载图片，完成后更新对应消息并刷新（不阻塞 UI）。"""
        def work():
            tmp = None
            try:
                import urllib.request
                ext = os.path.splitext(url.split("?")[0])[1] or ".png"
                tmp = os.path.join(tempfile.gettempdir(),
                                   "oi_chat_%d%s" % (int(time.time() * 1000), ext))
                urllib.request.urlretrieve(url, tmp)
            except Exception:
                tmp = None
            try:
                self._image_ready.emit(msg, tmp)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _apply_image(self, msg, tmp):
        try:
            if tmp and os.path.exists(tmp) and any(m is msg for m in self._messages):
                msg["image"] = tmp
                self.rebuild()
                if self.on_resize:
                    self.on_resize()
        except Exception:
            pass

    def set_thinking(self, on):
        self.set_status("思考中" if on else "")
        self._update_stop_btn()

    def _stop_thinking(self):
        if self._status_text or self._thinking:
            self._status_text = ""
            self._thinking = False
            self._dot_timer.stop()

    def set_status(self, text):
        """连接/接收状态提示（如"连接中…"、"接收中…"），带动态省略号。"""
        self._status_text = str(text)
        self._thinking = bool(text)
        self._dots = 0
        if text:
            if not self._dot_timer.isActive():
                self._dot_timer.start(400)
        else:
            self._dot_timer.stop()
        self.rebuild()

    def set_thinking_text(self, text):
        """显示大模型思考过程（reasoning_content），淡色、可折叠查看。"""
        self._thinking_text = str(text or "")
        self.rebuild()

    def append_stream(self, text):
        """流式期间累计回复文本并实时刷新显示（文本模式）。"""
        self._stop_thinking()
        self._streaming = True
        self._stream_text = str(text)
        self._stream_html = ""
        self._update_stop_btn()
        self.rebuild()
        if self.on_resize:
            self.on_resize()

    def append_stream_html(self, html):
        """流式期间刷新回复的原始 HTML（webchat 网页会话模式）。"""
        self._stop_thinking()
        self._streaming = True
        self._stream_html = str(html or "")
        self._stream_text = ""
        self._update_stop_btn()
        self.rebuild()
        if self.on_resize:
            self.on_resize()

    def end_stream(self, final_text, html=None):
        """流式结束：把完整回复作为正式消息入库并显示。"""
        self._streaming = False
        self._stream_text = ""
        self._stream_html = ""
        self._status_text = ""
        self._thinking = False
        self._dot_timer.stop()
        self._thinking_text = ""
        self._update_stop_btn()
        self.add_message("assistant", final_text, html=html)

    def _tick_thinking(self):
        self._dots = (self._dots + 1) % 4
        self.rebuild()

    def _send(self):
        # 并发守卫：上一条回复仍在思考/流式生成中时忽略再次发送。否则会起第二个
        # worker，覆盖 _chat_stop[name]、两路流式回复交错成乱码、停止按钮打不干净
        # （见气泡稳定性 H3）。用户要打断请先按停止按钮。
        if self._streaming or self._thinking:
            return
        text = self.input.toPlainText().strip()
        if (not text and not self._pending_images and not self._quoted) \
                or self.on_send is None:
            return
        self.input.clear()
        images = list(self._pending_images)
        self._pending_images = []
        if self._quoted:
            q = self._quoted
            quote_text = str(q.get("text", "")).replace("\n", " ")[:80]
            text = "[引用 %s] %s\n%s" % (
                "你" if q.get("role") == "user" else "AI", quote_text, text)
        if images:
            text = (text + "\n" if text else "") + \
                "\n".join("[图片: %s]" % p for p in images)
        self._clear_attachment()
        self.add_message("user", text, image_paths=images)
        self.on_send(text, images)

    def _clear_attachment(self, notify=True):
        """清空引用/待发送图片并隐藏附件条。"""
        self._quoted = None
        self._pending_images = []
        self._thumb_strip.set_images([])
        self._quote_text.setText("")
        self._quote_bar.hide()
        if notify and self.on_resize:
            self.on_resize()

    def _add_pasted_image(self, path):
        """粘贴图片入队：附件条显示迷你缩略图，可发送或删除。"""
        self._pending_images.append(path)
        self._thumb_strip.set_images(self._pending_images)
        self._quote_text.setText("已添加图片 %d 张" % len(self._pending_images))
        self._quote_bar.show()
        if self.on_resize:
            self.on_resize()

    def _set_quoted(self, idx):
        try:
            if not (0 <= idx < len(self._messages)):
                return
            m = self._messages[idx]
            label = "你" if m.get("role") == "user" else "AI"
            txt = str(m.get("text", "")).replace("\n", " ")[:60]
            self._quoted = dict(m)
            self._thumb_strip.set_images([])
            self._quote_text.setText("引用 %s：%s" % (label, txt))
            self._quote_bar.show()
            if self.on_resize:
                self.on_resize()
        except Exception:
            pass

    def _msg_index_at(self, pos):
        """右键位置对应的消息索引（按文档块号定位）。"""
        try:
            cursor = self.history.cursorForPosition(pos)
            n = cursor.block().blockNumber()
            idx = -1
            for i, s in enumerate(self._msg_block_starts):
                if s <= n:
                    idx = i
                else:
                    break
            if 0 <= idx < len(self._messages):
                return idx
        except Exception:
            pass
        return -1

    def rebuild(self):
        self._code_blocks = []
        msgs = self._messages
        if self._collapsed and msgs:
            msgs = msgs[-1:]
            self._update_collapsed_label()
            self.collapsed_label.show()
            self.history.hide()
        else:
            self.collapsed_label.hide()
            self.history.show()
        # 字体由历史框的 QFont 回退链负责（Qt 富文本的 CSS font-family 只取第一个，
        # 无法逐字回退特殊符号；用 setFamilies 才能让 ①②③ 等符号走覆盖它的字体）
        parts = ["<div style='font-size:10px;line-height:130%;'>"]
        if not msgs and not self._streaming and not self._thinking_text:
            # 空会话占位提示：切换/新建会话时能看出跳到了哪里
            parts.append("<div style='color:#6f7d96;font-size:10px;'>"
                         "（新对话，开始输入吧）</div>")
        img_w = max(_bs(60), min(_bs(150), self.width() - _bs(28)))   # 图片宽度随窗口自适应
        for mi, m in enumerate(msgs):
            if m["role"] == "user":
                color = "#4da3ff"
                label = "你"
            elif m["role"] == "tool":
                # 工具执行步骤：灰色小字，不参与引用/复制，仅作过程展示
                parts.append("<div style='color:#7f8ea8;font-size:10px;"
                             "line-height:13px;'>%s</div>"
                             % _html.escape(str(m.get("text", ""))))
                continue
            else:
                color = "#a8e6a3"
                label = "AI"
            if m.get("html"):
                # 网页会话原始 HTML：直接渲染（webchat 组件已清洗/绝对化图片链接）
                parts.append("<div><span style='color:%s;font-weight:600;'>%s：</span></div>"
                             "<div>%s</div>" % (color, label, m["html"]))
            else:
                parts.append("<div><span style='color:%s;font-weight:600;'>%s：</span>%s</div>"
                             % (color, label,
                                _chat_html(m["text"], self._code_blocks,
                                           collapse_long=True,
                                           expanded=self._expanded_code)))
            for im in (m.get("images") or []):
                parts.append("<div><img src='%s' width='%d'/></div>"
                             % (im.replace("\\", "/"), img_w))
            if m.get("image"):
                parts.append("<div><img src='%s' width='%d'/></div>"
                             % (m["image"].replace("\\", "/"), img_w))
        if self._thinking_text:
            # 思考过程：淡色、可折叠（只显示摘要行 + 展开全文）
            shown = self._thinking_text if len(self._thinking_text) <= 120 \
                else self._thinking_text[:117] + "…"
            parts.append("<div style='color:#8fa3c0;font-size:10px;'>"
                         "<a href='thinktoggle://' style='color:#8fa3c0;'>"
                         "▸ 思考：%s</a></div>" % _html.escape(shown))
        if self._streaming:
            if self._stream_html:
                # 网页会话流式：直接渲染原始 HTML（走共享尾部）
                parts.append("<div><span style='color:#a8e6a3;font-weight:600;'>AI：</span></div>"
                             "<div>%s<span style='color:#7db6ff;'>▍</span></div>"
                             % self._stream_html)
            else:
                # 流式回复：只渲染开头概览 + 实时字数，代码块自动折叠为"前 10 行预览"，
                # HTML 始终保持很小，不会卡 UI；完整内容生成结束后一次性显示（长代码仍折叠可展开）。
                st = self._stream_text
                total = len(st)
                if total > 1500:
                    head = st[:1500]
                    parts.append("<div><span style='color:#a8e6a3;font-weight:600;'>AI：</span>%s"
                                 "<span style='color:#7db6ff;'>▍</span></div>"
                                 % _chat_html(head, self._code_blocks,
                                              collapse_long=True,
                                              expanded=(), allow_expand=False))
                    parts.append("<div style='color:#6f7d96;font-size:10px;'>"
                                 "… 已输出 %d 字符（生成中，完成后显示完整内容）</div>" % total)
                else:
                    parts.append("<div><span style='color:#a8e6a3;font-weight:600;'>AI：</span>%s"
                                 "<span style='color:#7db6ff;'>▍</span></div>"
                                 % _chat_html(st, self._code_blocks,
                                              collapse_long=True,
                                              expanded=(), allow_expand=False))
        if self._thinking and not self._streaming:
            parts.append("<div><span style='color:#a8e6a3;font-weight:600;'>AI：</span>%s%s</div>"
                         % (self._status_text or "思考中", "•" * (self._dots + 1)))
        parts.append("</div>")
        # 记录重建前的滚动位置：流式/思考中用户上翻时不被强行拉回底部
        _sb = self.history.verticalScrollBar()
        _was_value = _sb.value()
        _was_at_bottom = _sb.value() >= _sb.maximum() - 6
        self.history.setHtml(_html_scale("".join(parts)))
        # 记录每条消息对应的起始块，供右键"引用"定位
        self._msg_block_starts = []
        try:
            block = self.history.document().begin()
            while block.isValid():
                t = block.text()
                if t.startswith("你：") or t.startswith("AI："):
                    self._msg_block_starts.append(block.blockNumber())
                block = block.next()
        except Exception:
            self._msg_block_starts = []
        sb = self.history.verticalScrollBar()
        _stream_like = self._streaming or bool(self._thinking_text)
        self._nav_lock = True
        try:
            if self._preserve_scroll:
                # 展开/收起代码块：保持当前滚动位置，不跳到最底部
                self._preserve_scroll = False
                sb.setValue(min(sb.maximum(), self._preserve_scroll_val))
            else:
                # 先同步恢复到重建前位置，避免 setHtml 把滚动条闪回顶部（思考/输出时不再"反弹"）
                sb.setValue(min(sb.maximum(), _was_value))
                if (not _stream_like) or _was_at_bottom:
                    # 非流式（新消息）或用户停在底部：延迟滚到最新底部跟随内容
                    QTimer.singleShot(0, self._scroll_to_last)
        finally:
            self._nav_lock = False
        # 内容刷新后：锚点取视口顶部的那条消息（通常在最新消息附近），
        # 上/下按钮从它开始逐条移动，保证每次只滚一条、始终有效
        self._nav_idx = self._current_top_msg_index()
        self._nav_active = True
        self._update_nav_btns()


# ==================== 待机情绪气泡 ====================

# 弹出气泡避让：所有弹出式小气泡共享同一布局，纵向堆叠、互不重合
_IMG_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s\"'<>]*)?", re.I)
_LINK_RE = re.compile(r"(https?://[^\s\u3000\uff0c\u3002]+)")


def _tool_step_text(name, args, result):
    """把一次工具调用格式化成一行可读的步骤说明。"""
    a = args or {}
    if name == "add_todo":
        label = "添加待办「%s」" % a.get("text", "")
    elif name == "delete_todo":
        label = "删除待办「%s」" % a.get("text", "")
    elif name == "remind":
        label = "设置提醒「%s」" % a.get("message", "")
    elif name == "add_module":
        label = "新建模块「%s」" % (a.get("rule") or {}).get("name", "")
    elif name == "remove_module":
        label = "删除模块「%s」" % a.get("name", "")
    elif name == "enable_module":
        label = "%s模块「%s」" % ("启用" if a.get("enabled", True) else "禁用",
                                  a.get("name", ""))
    elif name == "move_module":
        label = "调整模块顺序「%s」" % a.get("name", "")
    elif name == "add_button":
        label = "添加按钮「%s」" % a.get("name", "")
    elif name == "remove_button":
        label = "删除按钮「%s」" % a.get("name", "")
    elif name == "edit_button":
        label = "编辑按钮「%s」" % a.get("name", "")
    elif name == "pet_setting":
        label = "设置桌宠 %s=%s" % (a.get("key"), a.get("value"))
    elif name == "pet_control":
        label = "控制桌宠 %s" % a.get("action", "")
    elif name == "get_time":
        label = "查询时间"
    elif name == "open_url":
        label = "打开网页「%s」" % a.get("url", "")
    elif name == "list_todos":
        label = "查看待办"
    elif name == "list_modules":
        label = "查看模块"
    elif name == "list_buttons":
        label = "查看按钮"
    elif name == "list_components":
        label = "查看可用组件"
    elif name == "pet_info":
        label = "查看桌宠信息"
    else:
        label = "调用工具 %s" % name
    res = str(result or "").replace("\n", " ").strip()
    res = res[:36] + ("…" if len(res) > 36 else "")
    return "▸ %s → %s" % (label, res) if res else "▸ %s" % label


def _html_scale(s):
    """把富文本 HTML 里的 font-size/line-height:Npx 按气泡档位整体放大（根因统一，不逐个改）。"""
    try:
        from widgets import kit as _kit
        k = _kit.bubble_k()
    except Exception:
        k = 1.0
    if k <= 1.0:
        return s
    return re.sub(r"(font-size|line-height)\s*:\s*(\d+)px",
                  lambda m: "%s:%dpx" % (m.group(1),
                                         max(1, int(round(float(m.group(2)) * k)))),
                  s)


def _chat_html(text, code_blocks=None, collapse_long=False, expanded=None,
               allow_expand=True):
    """把聊天文本转成 HTML：代码块可复制、链接可点击、保留换行与空白。
    collapse_long=True 时，超过 10 行的代码块折叠为"前 10 行 + 共 N 行 [展开]"，展开/收起
    按钮都在底部；expanded 为已展开的代码块索引集合。"""
    if code_blocks is None:
        code_blocks = _chat_html._blocks
        _chat_html._blocks = []
    s = str(text)
    parts = re.split(r"```", s)
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            # 代码块：深色底 + 等宽字体 + 复制链接（像网页聊天）
            code = seg.strip("\n")
            # 去掉首行语言标识（如 ```python）
            first, _, rest = code.partition("\n")
            if first.strip() and " " not in first.strip() and len(first.strip()) <= 20:
                code = rest.strip("\n")
            copy = ""
            if code:
                try:
                    idx = len(code_blocks)
                    code_blocks.append(code)
                    lines = code.splitlines()
                    n = len(lines)
                    if collapse_long and n > 10 and not (expanded and idx in expanded):
                        # 折叠：前 10 行预览 + 底部"共 N 行 [展开]"（生成中不提供展开）
                        shown = "\n".join(lines[:10])
                        btn = ("<a href='expandcode:%d' style='color:#7db6ff;'>展开</a>" % idx
                               ) if allow_expand else "（生成中）"
                        copy = ("<table width='100%%' border='0' cellspacing='0' cellpadding='0'>"
                                "<tr><td style='border:1px solid rgba(255,255,255,45);"
                                "background:rgba(0,0,0,0.35);'>"
                                "<div style='text-align:right;padding:1px 3px;'>"
                                "<a href='copy:%d' style='color:#7db6ff;"
                                "border:1px solid rgba(125,182,255,120);border-radius:3px;"
                                "padding:0 5px;font-size:10px;text-decoration:none;'>⧉</a></div>"
                                "<pre style='margin:0;padding:2px 4px;white-space:pre-wrap;"
                                "font-family:Consolas,monospace;font-size:10px;"
                                "color:#b8e0a8;'>%s</pre>"
                                "</td></tr></table>"
                                "<div style='text-align:center;padding:1px;font-size:10px;"
                                "color:#8fa3c0;'>共 %d 行 %s</div>"
                                % (idx, _html.escape(shown), n, btn))
                    elif collapse_long and n > 10:
                        # 展开：完整代码 + 底部"收起"
                        copy = ("<table width='100%%' border='0' cellspacing='0' cellpadding='0'>"
                                "<tr><td style='border:1px solid rgba(255,255,255,45);"
                                "background:rgba(0,0,0,0.35);'>"
                                "<div style='text-align:right;padding:1px 3px;'>"
                                "<a href='copy:%d' style='color:#7db6ff;"
                                "border:1px solid rgba(125,182,255,120);border-radius:3px;"
                                "padding:0 5px;font-size:10px;text-decoration:none;'>⧉</a></div>"
                                "<pre style='margin:0;padding:2px 4px;white-space:pre-wrap;"
                                "font-family:Consolas,monospace;font-size:10px;"
                                "color:#b8e0a8;'>%s</pre>"
                                "</td></tr></table>"
                                "<div style='text-align:center;padding:1px;font-size:10px;'>"
                                "<a href='collapsecode:%d' style='color:#8fa3c0;'>收起</a></div>"
                                % (idx, _html.escape(code), idx))
                    else:
                        # 用 table 的 width 属性（Qt 不认 CSS 宽度）铺满整行，
                        # 背景/边框放在 td 上，能把整段代码框在一个框里
                        copy = ("<table width='100%%' border='0' cellspacing='0' cellpadding='0'>"
                                "<tr><td style='border:1px solid rgba(255,255,255,45);"
                                "background:rgba(0,0,0,0.35);'>"
                                "<div style='text-align:right;padding:1px 3px;'>"
                                "<a href='copy:%d' style='color:#7db6ff;"
                                "border:1px solid rgba(125,182,255,120);border-radius:3px;"
                                "padding:0 5px;font-size:10px;text-decoration:none;'>⧉</a></div>"
                                "<pre style='margin:0;padding:2px 4px;white-space:pre-wrap;"
                                "font-family:Consolas,monospace;"
                                "font-size:10px;color:#b8e0a8;'>%s</pre>"
                                "</td></tr></table>"
                                % (idx, _html.escape(code)))
                except Exception:
                    pass
            out.append(copy if copy else _html.escape(code))
        else:
            out.append(_chat_html_inline(seg))
    # 用 div 包裹（span 内不能放 table 等块级元素，会导致代码块被 Qt 丢弃）
    return "<div style='white-space:pre-wrap;'>%s</div>" % "".join(out)


def _chat_html_inline(seg):
    """普通段落：转义、行内代码、链接可点击、保留换行。"""
    s = _html.escape(seg)
    s = re.sub(r"`([^`]+)`",
               lambda m: ("<code style='background:rgba(255,255,255,25);border-radius:3px;"
                          "padding:0 3px;font-family:Consolas,monospace;font-size:10px;"
                          "color:#f0e68c;'>%s</code>" % m.group(1)), s)
    s = _LINK_RE.sub(lambda m: '<a href="%s">%s</a>' % (m.group(0), m.group(0)), s)
    s = s.replace("\n", "<br>")
    return s


_chat_html._blocks = []   # 最近一次渲染的代码块（供复制链接索引）


def _chat_history_path():
    """对话记录持久化文件路径（委托数据层统一解析）。"""
    return data_store.data_path("chat_history.json")


def _chat_summary_path():
    """对话摘要持久化文件路径（委托数据层统一解析）。"""
    return data_store.data_path("chat_summary.json")


def _split_links(value):
    """把文本按链接切分为 [(text, url_or_None), ...]，链接去掉尾部标点"""
    value = str(value)
    parts = []
    last = 0
    for m in _LINK_RE.finditer(value):
        url = m.group(1).rstrip(".,;:!?。，；：！？)]}”’»")
        if m.start() > last:
            parts.append((value[last:m.start()], None))
        if url:
            parts.append((url, url))
        last = m.end()
    if last < len(value):
        parts.append((value[last:], None))
    return parts or [(value, None)]


def _w_is_alive(w):
    """组件是否仍存活（deleteLater 后访问会抛 RuntimeError）。"""
    try:
        w.isVisible()
        return True
    except RuntimeError:
        return False


class _menu_hold:
    """右键菜单弹出期间防止气泡自动隐藏：进入时置 _menu_open，退出时恢复。
    鼠标移到菜单上会触发气泡 leaveEvent（开始隐藏倒计时），必须抑制。"""

    def __init__(self, bubble):
        self._bubble = bubble

    def __enter__(self):
        b = self._bubble
        try:
            b._menu_open = True
            b._hide_timer.stop()
            b._auto_hide_timer.stop()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        b = self._bubble
        try:
            b._menu_open = False
            # 菜单关闭后鼠标不在气泡上：恢复自动隐藏倒计时
            if not b.should_stay():
                b.schedule_hide(getattr(b, "_auto_dur_ms", 3000))
        except Exception:
            pass
        return False


def _widget_height(w, width):
    """交互组件行的推荐高度：按内容自适应（文本按宽度换行 / FIX_H / sizeHint），
    避免固定取 widget.height() 导致列表、文本等常用组件被裁切或留白。"""
    # 1) current_height 优先：它支持折叠/内容自适应，并已按逻辑档位换算。
    if hasattr(w, "current_height"):
        try:
            ch = int(w.current_height() or 0)
            if ch > 0:
                return max(10, ch)
        except Exception:
            pass
    # 2) FIX_H 是标准档逻辑高度，由框架统一换算，避免组件各乘一套比例。
    try:
        fh = int(getattr(w, "FIX_H", 0) or 0)
        if fh > 0:
            from widgets import kit
            return max(10, kit.bs(fh))
    except Exception:
        pass
    # 3) 文本/布局类控件按给定宽度计算换行高度
    try:
        hfw = int(w.heightForWidth(max(10, int(width))))
        if hfw > 0:
            return max(10, hfw)
    except Exception:
        pass
    try:
        sh = w.sizeHint()
        if sh is not None and sh.isValid() and sh.height() > 0:
            return max(10, sh.height())
    except Exception:
        pass
    return max(10, int(w.height() or 0))


def _style_module_widget(w):
    """给交互模块的根控件加卡片样式（浅底 + 细边框 + 圆角），仅旧版直接摆放时使用。"""
    try:
        if w is None:
            return
        w.setObjectName("moduleCard")
        qss = w.styleSheet() or ""
        if "moduleCard" not in qss:
            w.setStyleSheet(qss + "\n#moduleCard{"
                            "border:1px solid rgba(255,255,255,42);"
                            "border-radius:5px;background:rgba(255,255,255,11);}")
    except Exception:
        pass


class _HeadIconButton(QPushButton):
    """气泡右上角图标按钮（钉住 / 关闭）：图形几何居中绘制。

    原先用 "○"/"●"/"✕" 文字字形，Microsoft YaHei 的字形上下留白不对称，小档位下
    看起来与透明度滑块不在同一水平线。改为自己按控件中心画，任何档位都精确居中。
    """

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind          # "pin" | "close"
        self._active = False       # 钉住态
        self._hover = False
        self.setFlat(True)

    def setText(self, text):
        # 兼容既有调用：外部仍用 setText("●"/"○") 表达钉住状态
        t = str(text or "")
        if self._kind == "pin":
            self._active = ("●" in t)
        self.update()

    def text(self):
        if self._kind == "pin":
            return "●" if self._active else "○"
        return "✕"

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            if self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 40))
                r = max(2, int(round(min(w, h) * 0.2)))
                p.drawRoundedRect(0, 0, w, h, r, r)
            col = QColor(255, 255, 255) if self._hover else QColor(200, 215, 240, 190)
            cx, cy = w / 2.0, h / 2.0            # 几何中心，精确居中
            rad = max(2.0, min(w, h) * 0.26)
            pen_w = max(1.0, min(w, h) * 0.09)
            if self._kind == "pin":
                p.setPen(QPen(col, pen_w))
                p.setBrush(col if self._active else Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            else:
                p.setPen(QPen(col, pen_w, Qt.SolidLine, Qt.RoundCap))
                d = rad * 0.92
                p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
                p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
        finally:
            p.end()


class StatusBubble(QWidget):
    """系统状态气泡基类：模型层（规则/刷新/聊天/工具桥/右键菜单）+ 旧手绘呈现。

    注意：呈现层已退役——桌宠实际只使用子类 StatusBubbleLayout（bubble_layout.py），
    它覆盖了 _relayout/_render_cache/mouse 系列。本类的旧手绘路径（_rows/_row_y/
    _link_hits/_drag/_find_handle_row/_drag_offset 等）为历史遗留，已不可达，
    仅供回退兜底与阅读参考，不要再为它新增功能。
    """

    _LAYOUT = False   # 布局版气泡（bubble_layout.StatusBubbleLayout）覆盖为 True
    _ROW_H = 15
    _ROW_TITLE_H = 15  # 组件行标题栏高度（对话面板自带标题栏，不占此高）
    _HEAD_H = 20
    _FIX_W = 210    # 气泡固定宽度：内容不会把气泡撑大
    _rule_done = pyqtSignal(str, str)  # 后台规则线程完成 (name, text)
    _chat_done = pyqtSignal(str, str)  # 对话回复完成 (name, text)
    _chat_chunk = pyqtSignal(str, str) # 流式对话增量 (name, 累计文本)
    _chat_think = pyqtSignal(str, str) # 思考过程增量 (name, 累计思考文本)
    _chat_status = pyqtSignal(str, str) # 连接/接收状态 (name, 状态文本)
    _tool_step = pyqtSignal(str, str)   # 工具执行步骤可视化 (name, 步骤文本)
    _btn_qss = ("QPushButton{border:none;background:transparent;color:rgba(200,215,240,190);"
                "font-size:10px;padding:0;} QPushButton:hover{color:#ffffff;"
                "background:rgba(255,255,255,40);border-radius:3px;}")

    def __init__(self, pet_widget):
        super().__init__(None)
        self.setProperty("oi_nozoom", True)   # 已按 kit.bs()/ps() 自行放大，不参与统一放大
        self.pet = pet_widget
        # 气泡档位缩放：行高/标题/宽度/字体按 bubble_scale 放大（设置窗口"气泡大小"）
        from widgets import kit as _kit
        self._ROW_H = _kit.bubble_token("row_height")
        self._ROW_TITLE_H = _kit.bubble_token("row_height")
        self._HEAD_H = _kit.bubble_token("head_height")
        self._FIX_W = _kit.bubble_token("width")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._font = QFont("Microsoft YaHei")
        self._font.setPointSizeF(7.5 * _kit.bubble_k())  # 与对话文字大小一致
        self._rows = []       # [(title, lines, row_h)]（lines 为 [(text, url), ...]）
        self._raw_rows = []   # 原始行 [(title, value)]
        self._disp_rows = []  # 实际显示行 (title, value, widget)，布局的输入
        self._last = {}       # provider.name -> (更新时间, 文本)
        self._enabled = {}    # provider.name -> bool
        self._custom = []     # 旧版静态项（已迁移为规则）
        self._rules = []      # RuleProvider 列表
        self._all_rule_cfgs = []  # 全部规则配置（含禁用，用于内置项排除）
        self._pending_rules = set()
        from module_core import ModuleScheduler
        self._module_scheduler = ModuleScheduler()
        self._rule_done.connect(self._on_rule_done)
        self._chat_done.connect(self._on_chat_done)
        self._chat_chunk.connect(self._on_chat_chunk)
        self._chat_think.connect(self._on_chat_think)
        self._chat_status.connect(self._on_chat_status)
        self._tool_step.connect(self._on_tool_step)
        self._pinned = False
        self._mouse_over = False
        self._auto_showing = False
        self._auto_dur_ms = 3000   # 自动弹出的显示时长（毫秒）
        self._popup_pool = {}   # 规则名 -> 独立浮出气泡（多条规则可同时弹出并自动避让）
        self._err_popup = _st_monitor("ErrorPopup")(self.pet)   # 错误提示弹窗
        self._err_notified = set()                   # 已提示过错误的规则
        self._link_hits = []
        self._agent_map = {}   # 行标题 -> 智能体规则（供审批按钮）
        self._title_w = _kit.bs(44)   # 标题列宽随气泡档位缩放（值区宽度按它扣除）
        self._full_h = 120
        self._cached_pm = None   # 内容缓存位图（弹出动画平滑）
        self._pending_titles = set()  # 正在加载/思考中，显示动态图标
        self._scroll_titles = set()   # 内容超宽，滚动显示
        self._ui_timer = QTimer(self)
        self._ui_timer.setTimerType(Qt.PreciseTimer)
        self._ui_timer.timeout.connect(self._ui_tick)
        # 右上角小按钮：钉住（简单 logo，无颜色）/ 关闭（几何居中绘制）
        self._pin_btn = _HeadIconButton("pin", self)
        self._fold_btn = _HeadIconButton("close", self)
        for b in (self._pin_btn, self._fold_btn):
            b.setFixedSize(_kit.bs(16), _kit.bs(16))
            b.setCursor(Qt.PointingHandCursor)
        self._pin_btn.clicked.connect(self._toggle_pin)
        self._fold_btn.clicked.connect(self._close_bubble)
        self._pin_btn.setToolTip("钉住气泡（一直显示并跟随桌宠）")
        self._fold_btn.setToolTip("关闭气泡")
        # 上下展开/收起动画
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.PreciseTimer)
        self._anim_timer.timeout.connect(self._height_tick)
        self._anim_h0 = 0.0
        self._anim_h1 = 0.0
        self._anim_t0 = 0.0
        self._anim_dur = 0.18
        self._anim_bottom = 0
        self._anim_dir = 0   # 1=展开, -1=收起, 0=静止
        # 内容更新后的平滑补位（避免加载完成时尺寸突变）
        self._settle_active = False
        self._settle_x1 = self._settle_y1 = 0
        self._settle_w1 = self._settle_h1 = 0
        self._settle_x2 = self._settle_y2 = 0
        self._settle_w2 = self._settle_h2 = 0
        self._settle_t0 = 0.0
        # 淡入淡出
        self._fade_timer = QTimer(self)
        self._fade_timer.setTimerType(Qt.PreciseTimer)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_active = False
        self._fade_from = 0.0
        self._fade_to = 1.0
        self._fade_dur = 160.0
        self._fade_t0 = 0.0
        self._fade_done = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_delayed_hide)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._on_auto_hide)
        self._menu_open = False   # 右键菜单弹出中：抑制自动隐藏
        self._mv_target = (0, 0)
        self._mv_from = None          # 侧切换动画起点（None=未在动画中）
        self._mv_t0 = 0.0             # 动画开始时刻
        self._mv_dur = 0.3            # 侧切换移动动画时长（秒）
        self._side_hint = None        # 气泡当前稳定侧（above/below/left/right）；None=待判定
        self._mv_timer = QTimer(self)
        self._mv_timer.setTimerType(Qt.PreciseTimer)
        self._mv_timer.timeout.connect(self._mv_tick)
        self._inter_widgets = []     # 交互组件（对话面板/番茄卡片等），按规则顺序
        # LLM 工具提醒桥：工具线程里设置定时提醒，主线程弹气泡
        try:
            from status_monitor import _tool_bridge
            _tool_bridge.popup.connect(self._on_tool_reminder)
            _tool_bridge.command.connect(self._on_tool_command)
        except Exception:
            pass
        self._widget_geos = []       # (widget, x, y, w, h)：最近一次布局位置
        self._chat_pending = {}      # 对话规则名 -> 正在等待的回复数（防止连发丢回复）
        self._chat_stop = {}         # 对话规则名 -> threading.Event（停止按钮中断）
        self._thread_slots = threading.BoundedSemaphore(8)  # 模块线程并发上限
        self._stream_acc = {}        # 流式回复累积（批量刷新，避免长代码每块整屏重建卡顿）
        self._think_acc = {}         # 思考过程累积
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(80)
        self._stream_timer.timeout.connect(self._flush_stream)
        self._card_host = None       # H5 卡片容器（延迟创建，供后续功能开发）
        self._drag = None            # 模块拖拽排序状态
        self._row_y = []             # 每行基础 y（与 _rows 对应）
        self._row_rule_ids = []      # 每行对应的规则 id（可拖拽行）
        self._row_states = []        # 每行健康状态（正常/加载/过期/错误/暂停）
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)
        self.hide()

    # ---- 钉住 / 收起 ----
    def _toggle_pin(self):
        self._pinned = not self._pinned
        self._pin_btn.setText("●" if self._pinned else "○")
        self._pin_btn.setToolTip("取消钉住" if self._pinned else "钉住气泡（一直显示并跟随桌宠）")
        if self._pinned:
            self._auto_hide_timer.stop()
            self._hide_timer.stop()
        self.update()

    def unpin(self):
        """解除钉住（气泡保持）。唯一一处改 `_pinned=False` 并同步按钮外观的地方，
        免得每个调用点各写一份、漏掉按钮上的 ●/○。"""
        if not self._pinned:
            return
        self._pinned = False
        self._pin_btn.setText("○")
        self._pin_btn.setToolTip("钉住气泡（一直显示并跟随桌宠）")
        self.update()

    def _close_bubble(self):
        """关闭气泡（=收起），同时解除钉住"""
        self.unpin()
        self._auto_hide_timer.stop()
        self._hide_timer.stop()
        # 关闭后抑制悬停重弹：光标需离开桌宠再回来才会再次弹出
        # （否则光标一直悬在桌宠上，帧循环会立刻把它重新弹出来，关了个寂寞）
        try:
            self.pet._click_suppress = True
        except Exception:
            pass
        self.hide_animated()

    def should_stay(self):
        return (self._pinned or self._mouse_over or self._auto_showing
                or self._menu_open or self._any_chat_focus()
                or self._has_modal_popup())

    def _has_modal_popup(self):
        """气泡内模块弹出了窗口（AI 绘画输入框/确认框）时保持显示。"""
        try:
            from PyQt5.QtWidgets import QApplication as _QA
            widgets = {it.get("widget") for it in self._inter_widgets
                       if it.get("widget") is not None}
            if not widgets:
                return False
            for tl in _QA.topLevelWidgets():
                if not tl.isVisible() or tl is self:
                    continue
                # 弹窗自身或其父链含气泡内组件 → 属于气泡的弹窗
                cur = tl
                while cur is not None:
                    if cur in widgets:
                        return True
                    cur = cur.parent()
            return False
        except Exception:
            return False

    def start_hide_countdown(self):
        """拖动/点击/离开桌宠：开始 3 秒消失倒计时（聊天输入时不触发）"""
        if self._pinned or self._any_chat_focus():
            return
        self._auto_hide_timer.start(3000)

    def _any_chat_focus(self):
        try:
            for it in self._inter_widgets:
                w = it.get("widget")
                if isinstance(w, ChatPanel) and w.input.hasFocus():
                    return True
        except Exception:
            pass
        return False

    def schedule_hide(self, delay_ms=220):
        """延迟隐藏：给鼠标从桌宠移到气泡留时间（钉住/悬停气泡时不隐藏）"""
        self._hide_timer.start(delay_ms)

    def _on_delayed_hide(self):
        if not self.should_stay():
            # 鼠标刚离开气泡：若此时正落在桌宠上（悬停/按住/拖拽），保持显示
            try:
                if self.pet.underMouse() or self.pet._cursor_over_pet():
                    return
            except Exception:
                pass
            self.hide_animated()

    def _on_auto_hide(self):
        self._auto_showing = False
        if not self.should_stay():
            self.hide_animated()

    # ---- 显隐 ----
    def prepare_for_rebuild(self):
        """缩放热更新前释放可等待资源，避免新旧气泡同时持有聊天/模块状态。"""
        # 断开全局 _tool_bridge 连接并停掉常驻定时器：否则本气泡被外部信号连接
        # 持有引用无法 GC，其 500ms 主定时器会继续跑调度并起后台线程，每次缩放
        # 热重建都叠加一个"僵尸气泡"持续轮询/起线程（内存与 CPU 泄漏）。
        try:
            from status_monitor import _tool_bridge
            try:
                _tool_bridge.popup.disconnect(self._on_tool_reminder)
            except Exception:
                pass
            try:
                _tool_bridge.command.disconnect(self._on_tool_command)
            except Exception:
                pass
        except Exception:
            pass
        for _tn in ("_timer", "_stream_timer", "_anim_timer", "_mv_timer",
                    "_fade_timer", "_ui_timer", "_dot_timer", "_fold_timer",
                    "_save_timer", "_hide_timer", "_auto_hide_timer"):
            _t = getattr(self, _tn, None)
            if _t is not None:
                try:
                    _t.stop()
                except Exception:
                    pass
        for name in list(getattr(self, "_chat_stop", {})):
            try:
                self._stop_chat(name)
            except Exception:
                pass
        self._chat_stop.clear()
        self._chat_pending.clear()
        self._pinned = False
        self._mouse_over = False
        self._refreshing = True
        self._refresh_pending = False
        self.hide()
        try:
            self._err_popup.hide_animated()
        except Exception:
            pass
        for popup in list(self._popup_pool.values()):
            try:
                popup.hide()
                popup.deleteLater()
            except Exception:
                pass
        self._popup_pool.clear()
        for it in self._inter_widgets:
            try:
                it.get("widget").deleteLater()
            except Exception:
                pass
        self._inter_widgets.clear()
        self._rules.clear()
        self._module_scheduler.reset()
        self._pending_rules.clear()
        self._disp_rows.clear()
        self._raw_rows.clear()
        self._row_states.clear()
        if getattr(self, "_row_widgets", None):
            for wrap in list(self._row_widgets):
                try:
                    wrap.deleteLater()
                except Exception:
                    pass
            self._row_widgets.clear()
        if getattr(self, "_wrap_cache", None):
            for wrap in list(self._wrap_cache.values()):
                try:
                    wrap.deleteLater()
                except Exception:
                    pass
            self._wrap_cache.clear()
        self._last_keys = None
        self.setUpdatesEnabled(False)

    def hide(self):
        self._fade_active = False
        self._fade_timer.stop()
        self._mv_timer.stop()
        self._ui_timer.stop()
        for it in self._inter_widgets:
            try:
                it["widget"].hide()
            except Exception:
                pass
        if self._card_host is not None:
            self._card_host.hide()
        self.setWindowOpacity(1.0)
        super().hide()

    # ---- H5 拓展基础（后续功能开发入口） ----
    def show_cards(self, cards):
        """在气泡内容区显示一组 H5 卡片。"""
        if self._card_host is None:
            from h5_cards import CardHost
            self._card_host = CardHost(self, k=_kit_scale())
        self._card_host.show_cards(cards)
        self._relayout()

    def hide_cards(self):
        if self._card_host is not None:
            self._card_host.hide()

    def _layout_card_host(self):
        if self._card_host is not None and self._card_host.cards():
            self._card_host.setGeometry(4, self._HEAD_H + 2,
                                        max(20, self.width() - 8),
                                        max(30, self.height() - self._HEAD_H - 4))
            self._card_host.show()
            self._card_host.raise_()
        elif self._card_host is not None:
            self._card_host.hide()

    def hide_animated(self):
        """向下收起动画后隐藏；钉住时不隐藏"""
        if self._pinned:
            return
        if not self.isVisible():
            return
        # 收起前清理任何进行中的补位/展开动画，避免状态串扰
        self._settle_active = False
        self._anim_timer.stop()
        self._play_height(False)

    def _play_height(self, to_full):
        self._settle_active = False   # 播放动画期间禁用补位模式
        self._anim_dir = 1 if to_full else -1
        self._anim_h0 = float(self.height())
        self._anim_h1 = float(self._full_h if to_full else 0.0)
        self._anim_t0 = time.monotonic()
        self._anim_dur = ANIM_OPEN_DUR
        self._anim_timer.start(ANIM_MS)

    def _height_tick(self):
        if self._settle_active:
            k = min(1.0, (time.monotonic() - self._settle_t0) / ANIM_SETTLE_DUR)
            e = ease_in_out(k)
            w = int(self._settle_w1 + (self._settle_w2 - self._settle_w1) * e)
            h = int(self._settle_h1 + (self._settle_h2 - self._settle_h1) * e)
            x = int(self._settle_x1 + (self._settle_x2 - self._settle_x1) * e)
            y = int(self._settle_y1 + (self._settle_y2 - self._settle_y1) * e)
            self.setGeometry(x, y, w, h)
            if k >= 1.0:
                self._settle_active = False
                self._anim_timer.stop()
                self._place()
            return
        k = min(1.0, (time.monotonic() - self._anim_t0) / self._anim_dur)
        e = ease_out(k)
        h = int(self._anim_h0 + (self._anim_h1 - self._anim_h0) * e)
        self._apply_height(h)
        # 透明度随动画同帧变化：展开淡入、收起淡出，单定时器驱动
        op = getattr(self, "_bubble_opacity", 0.95)
        if self._anim_dir > 0:
            self.setWindowOpacity(0.0 + op * e)
        else:
            self.setWindowOpacity(max(0.0, op - op * e))
        if k >= 1.0:
            self._anim_timer.stop()
            self._anim_dir = 0
            if self._anim_h1 <= 0:
                self.hide()
            else:
                self._apply_height(int(self._anim_h1))

    def _apply_height(self, h):
        """保持底边不动，只改高度（向上展开 / 向下收起）"""
        self.setGeometry(self.x(), self._anim_bottom - h, self.width(), h)

    def _start_settle(self, w2, h2):
        """内容更新后的平滑补位：按当前侧过渡到新几何（宽度/高度/位置）。

        位置用 _compute_target_geom 计算（考虑当前侧与屏幕边界）——不能像原来
        那样固定"水平居中于桌宠"，否则两侧的气泡在展开/收起模块时会被补位到
        桌宠图标上方（"先移动到桌宠上"），再被 _place 拉回右侧。"""
        try:
            _side = self._side_hint or self._current_side()
            x2, y2, _mode = self._compute_target_geom(h2, _side)
        except Exception:
            bottom = self.y() + self.height()
            pc = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, 0))
            x2 = pc.x() - w2 // 2
            scr = QApplication.screenAt(pc) or QApplication.primaryScreen()
            g = scr.availableGeometry()
            x2 = max(g.left(), min(x2, g.right() - w2))
            y2 = bottom - h2
        self._settle_active = True
        self._settle_x1, self._settle_y1 = self.x(), self.y()
        self._settle_w1, self._settle_h1 = self.width(), self.height()
        self._settle_x2, self._settle_y2 = x2, y2
        self._settle_w2, self._settle_h2 = w2, h2
        self._settle_t0 = time.monotonic()
        self._anim_timer.start(ANIM_MS)

    def enterEvent(self, e):
        self._mouse_over = True
        try:
            # 按住/拖拽桌宠时进入气泡不取消倒计时（避免气泡一直不消失）
            if self.pet.is_dragging or self.pet._is_pressed:
                pass
            else:
                self._hide_timer.stop()
                self._auto_hide_timer.stop()
        except Exception:
            self._hide_timer.stop()
            self._auto_hide_timer.stop()
        if self._anim_dir < 0 and self.isVisible():
            self._play_height(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._mouse_over = False
        if not self._pinned:
            # 鼠标离开气泡：按自动弹出时长重新计时（悬停时不消失）
            self.schedule_hide(getattr(self, '_auto_dur_ms', 3000))
        super().leaveEvent(e)

    def _fade(self, start, end, dur, on_done=None):
        self._fade_active = True
        self._fade_from = float(start)
        self._fade_to = float(end)
        self._fade_dur = float(max(1, dur)) / 1000.0
        self._fade_t0 = time.monotonic()
        self._fade_done = on_done
        self.setWindowOpacity(self._fade_from)
        if not self._fade_timer.isActive():
            self._fade_timer.start(16)

    def _fade_tick(self):
        if not self._fade_active:
            self._fade_timer.stop()
            return
        t = min(1.0, (time.monotonic() - self._fade_t0) / self._fade_dur)
        eased = 1.0 - (1.0 - t) ** 3
        self.setWindowOpacity(self._fade_from + (self._fade_to - self._fade_from) * eased)
        if t >= 1.0:
            self._fade_active = False
            self._fade_timer.stop()
            if self._fade_done:
                done = self._fade_done
                self._fade_done = None
                done()

    def set_config(self, status_enabled=None, custom_items=None, rules=None):
        """由设置窗口下发：哪些提供者启用、旧版静态项、用户规则列表。"""
        if status_enabled is not None:
            self._enabled = dict(status_enabled)
        if custom_items is not None:
            self._custom = list(custom_items)
        if rules is not None:
            # 只加载启用的规则（禁用后气泡里立即消失，间隔也不再轮询）
            from module_core import normalize_rules
            self._all_rule_cfgs = normalize_rules(rules)
            self._rules = [_st_monitor("RuleProvider")(r)
                           for r in self._all_rule_cfgs
                           if r.get("enabled", True)]
            self._module_scheduler.reset()
            self._pending_rules.clear()
            self._rebuild_interactive()
        self._refresh()
        if self.isVisible():
            self._relayout()
            self._place()
            self.update()

    def _rebuild_interactive(self):
        """按规则顺序为对话/交互规则创建组件（对话面板、番茄卡片等）。"""
        try:
            # 先清空行引用，避免重建过程中被删除的旧组件被布局误引用
            self._disp_rows = []
            self._rows = []
            old = {}
            for it in self._inter_widgets:
                old[it.get("key")] = it
            new_list = []
            for idx, p in enumerate(self._rules):
                key = p.name   # 用规则名作键：排序变化时组件可复用，不闪烁/失效
                spec = p.spec
                # 对话模式建聊天面板；动作模块走动作行；其他 ui 加载组件插件。
                src = spec.source
                ui = spec.view.ui
                if spec.is_chat:
                    widget = old.get(key, {}).get("widget") if old.get(key) else None
                    if widget is None or not isinstance(widget, ChatPanel):
                        widget = ChatPanel(self)
                        widget.on_resize = self._relayout
                        widget._bubble = self   # 折叠动画时让气泡即时跟尺寸
                    widget.on_send = (lambda text, images=None, pp=p: self._send_chat(pp, text, images))
                    widget.on_stop = (lambda n=p.name: self._stop_chat(n))
                    widget.set_provider(p)
                    if src.get("type") == "llm":
                        # llm（AI 助手）模块加标题栏 + 右侧模型名，与 webchat 区分
                        try:
                            widget.set_header(p.title or p.name, None,
                                              right_text=str(src.get("model") or ""))
                        except Exception:
                            pass
                    if not self._LAYOUT:
                        _style_module_widget(widget)
                    new_list.append({"key": key, "title": p.title, "widget": widget,
                                     "provider": p, "h": widget.current_height(),
                                     "chat": True})
                elif spec.view.ui:
                    if spec.is_action:
                        # 动作模块走 bubble_layout._LBtnRow，不建嵌入组件：
                        # 与文本行同高、标题样式一致。
                        continue
                    widget = old.get(key, {}).get("widget") if old.get(key) else None
                    if ui == "pomodoro":
                        from h5_cards import PomodoroCard
                        if widget is None or not isinstance(widget, PomodoroCard):
                            # 卡片使用独立状态，避免与后台规则线程并发写同一字典
                            widget = PomodoroCard(state=None, parent=self, k=_kit_scale())
                    else:
                        # 自定义组件（widgets/ 目录）：主线程创建，异常只影响该模块
                        from widgets import load_module_widget
                        if widget is None or getattr(widget, "_ui_name", "") != ui:
                            nw, werr = load_module_widget(ui, self)
                            if nw is None:
                                p._last_error = werr or "组件 %s 加载失败" % ui
                                widget = None
                            else:
                                widget = nw
                                widget._ui_name = ui
                                widget.set_rule(p.rule)
                                # 组件高度变化（如收起/展开）时通知气泡重排。
                                # 走 _on_widget_resize 而不是直接 _relayout：
                                # 后者是瞬间重建，组件自己收起时就"一帧跳回去"。
                                try:
                                    widget.on_resize = (
                                        lambda w=widget: self._on_widget_resize(w))
                                except Exception:
                                    pass
                                try:
                                    widget.render(getattr(widget, "state", {}),
                                                  self._last.get(p.name, (0, ""))[1] or "")
                                except Exception:
                                    pass
                    if widget is not None:
                        if not self._LAYOUT:
                            _style_module_widget(widget)
                        new_list.append({"key": key, "title": p.title, "widget": widget,
                                         "provider": p, "h": widget.height(), "chat": False})
            # 按组件身份清理：h/标题等字段变化不误删被复用的组件
            new_widget_ids = {id(w["widget"]) for w in new_list}
            for it in self._inter_widgets:
                if id(it["widget"]) not in new_widget_ids:
                    try:
                        it["widget"].hide()
                        it["widget"].deleteLater()
                    except Exception:
                        pass
            self._inter_widgets = new_list
        except Exception:
            # 组件重建是气泡的核心路径；这里失败必须留下证据，禁止静默降级。
            try:
                import traceback
                with open(os.path.join(tempfile.gettempdir(),
                                       "oi_pet_error.log"), "a",
                          encoding="utf-8") as f:
                    f.write("\n[%s] bubble module rebuild failed\n" %
                            time.strftime("%Y-%m-%d %H:%M:%S"))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _visible_providers(self):
        # 已转成内置规则的系统项（cpu/memory/battery/network）不再作为独立提供者显示；
        # 用户删除过的内置（hidden_builtins）对应系统项也一并隐藏
        builtin_keys = {r.get("builtin") for r in getattr(self, '_all_rule_cfgs', [])
                        if r.get("builtin")}
        hidden = set(getattr(self.pet, "settings", {}).get("hidden_builtins", []) or [])
        # hidden_builtins 里是规则 id（如 builtin_cpu）→ 提取内置名（cpu）
        hidden_names = set()
        for h in hidden:
            s = str(h)
            if s.startswith("builtin_"):
                hidden_names.add(s[len("builtin_"):])
        return [p for p in _st_monitor("get_providers")()
                if p.name != "disk" and p.name not in builtin_keys
                and p.name not in hidden_names
                and self._enabled.get(p.name, True)]

    def _refresh(self):
        # 防止后台规则线程完成信号在 set_config/渲染中途重入 _refresh
        if getattr(self, '_refreshing', False):
            self._refresh_pending = True
            return
        self._refreshing = True
        try:
            self._refresh_impl()
        finally:
            self._refreshing = False
        if getattr(self, '_refresh_pending', False):
            self._refresh_pending = False
            QTimer.singleShot(0, self._refresh)

    def _refresh_impl(self):
        now = time.monotonic()
        changed = False
        self._pending_titles = set()
        self._agent_map.clear()
        rows = []
        row_rule_ids = []
        row_states = []
        for p in self._visible_providers():
            entry = self._last.get(p.name)
            if entry is None or now - entry[0] >= p.interval:
                try:
                    val = p.collect()
                except Exception:
                    val = "—"
                self._last[p.name] = (now, val)
                changed = True
            else:
                val = entry[1]
            rows.append((p.title, val, None))
            row_rule_ids.append(None)
            row_states.append(self._provider_state(
                p, self._last.get(p.name), False, now))
        # 用户规则：按各自间隔后台刷新（无论是否可见，供规则自动弹出）
        widget_by_name = {}
        for it in self._inter_widgets:
            widget_by_name[it["provider"].name] = it["widget"]
        for p in self._rules:
            entry = self._last.get(p.name)
            spec = p.spec
            pending = (entry is None or p.name in self._pending_rules)
            # 调度器统一处理首次加载、间隔、并发去重和失败退避；
            # chat 规则只在用户发消息后取数，避免离线时消耗 token。
            if self._module_scheduler.should_run(p, None if entry is None else entry[0], now):
                if (p.name not in self._pending_rules
                        and p.name not in self._chat_pending
                        and not getattr(p, "_hung", False)):
                    if spec.is_local:
                        # 时钟/静态文本这类纯本地计算：直接算，不起线程。
                        # 起线程 + 跨线程投递的开销比这点计算大得多；时钟按秒
                        # 刷新时更是每秒一个线程，纯属浪费。
                        self._module_scheduler.started(p.name)
                        try:
                            self._last[p.name] = (now, p.collect())
                            failed = False
                        except Exception:
                            self._last[p.name] = (now, "—")
                            failed = True
                        self._module_scheduler.finish(p.name, failed, now)
                    else:
                        self._pending_rules.add(p.name)
                        if self._spawn_rule_thread(p):
                            self._module_scheduler.started(p.name)
                        else:
                            # 没有空闲线程槽位：下个 tick 自动重试
                            self._pending_rules.discard(p.name)
            e2 = self._last.get(p.name)
            if not p.rule.get("embed", True):
                # 未勾选"嵌入气泡"：不显示行，但继续后台取数供自动弹出
                continue
            widget = widget_by_name.get(p.name)
            title = p.title
            if widget is None and spec.is_action:
                # 动作模块不是嵌入面板，而是一行“标题 + 动作按钮”。
                from bubble_layout import _BTN_MARK
                rows.append((title, "", _BTN_MARK))
                row_rule_ids.append(p.rule.get("id"))
                row_states.append(self._provider_state(p, e2, pending, now))
                continue
            if widget is not None:
                # 交互规则：标题占位行，内容由下方组件展示。
                # canvas / tokenmeter / stats / perler 等组件
                # 组件自带标题行，不再重复显示规则标题（否则出现两行）
                if not spec.view.title_visible:
                    title = ""
                rows.append((title, "", widget))
            else:
                rows.append((title, e2[1] if e2 else "", None))
                if pending and (not e2 or not e2[1]):
                    self._pending_titles.add(title)
                if (spec.source.get("type") == "agent" and p.pending_request()
                        and e2 and e2[1].startswith("待审批")):
                    self._agent_map[title] = p
            row_rule_ids.append(p.rule.get("id"))
            row_states.append(self._provider_state(p, e2, pending, now))
            continue
        for item in self._custom:
            rows.append((item.get("name", "自定义"), item.get("value", ""), None))
            row_rule_ids.append(None)
            row_states.append({"kind": "ok", "title": item.get("name", "自定义")})
        self._raw_rows = rows
        self._row_rule_ids = row_rule_ids
        self._row_states = row_states
        if self.isVisible():
            if not self._anim_dir and not getattr(self, "_fold_locked", False):
                # 全部规则常驻显示，不轮播、不突然增减
                self._disp_rows = rows
                self._relayout()
                if not self._settle_active:
                    self._place()
            if changed:
                self.update()
        else:
            # 未显示时保存当前行（弹出时直接使用）
            self._disp_rows = rows

    def _provider_state(self, provider, entry, pending=False, now=None):
        """Build a compact health snapshot for the bubble row and diagnostics."""
        now = time.monotonic() if now is None else now
        interval = max(5.0, float(getattr(provider, "interval", 60) or 60))
        error = str(getattr(provider, "_last_error", "") or "").strip()
        hung = bool(getattr(provider, "_hung", False))
        is_chat = bool(getattr(provider, "spec", None)
                       and provider.spec.is_chat)
        chat_waiting = is_chat and provider.name in self._chat_pending
        if hung or ("超过" in error and "暂停" in error):
            kind = "paused"
        elif error:
            kind = "error"
        elif is_chat and not chat_waiting:
            kind = "idle"
            error = ""
        elif pending:
            kind = "loading"
        elif entry is None:
            kind = "loading"
        else:
            age = max(0.0, now - float(entry[0]))
            stale_after = max(20.0, interval * 2.0 + 5.0)
            kind = "idle" if is_chat else ("stale" if age > stale_after else "ok")
            if is_chat:
                error = ""
        age = 0.0 if entry is None else max(0.0, now - float(entry[0]))
        retry_in = max(0, int(round(interval - age))) if kind in ("error", "stale") else None
        backoff = self._module_scheduler.retry_in(provider.name, now)
        if kind in ("error", "stale") and backoff > 0:
            retry_in = max(int(retry_in or 0), backoff)
        return {
            "kind": kind,
            "title": getattr(provider, "title", provider.name),
            "provider": provider.name,
            "error": error[:400],
            "age": int(age),
            "interval": int(interval),
            "retry_in": retry_in,
            "failures": self._module_scheduler.failure_count(provider.name),
        }

    def _show_provider_diag(self, provider):
        """Right-click diagnostics: expose refresh timing and the last failure."""
        state = self._provider_state(
            provider, self._last.get(provider.name),
            provider.name in self._pending_rules)
        from widgets import kit
        label = kit.health_token(state["kind"], "label")
        retry = state.get("retry_in")
        lines = [
            "模块：%s" % state["title"],
            "状态：%s" % label,
            "上次更新：%d 秒前" % state["age"],
            "刷新间隔：%d 秒" % state["interval"],
        ]
        if retry is not None:
            lines.append("下次刷新：%d 秒后" % retry)
        if state.get("failures"):
            lines.append("连续失败：%d 次" % state["failures"])
        lines.append("错误：%s" % (state["error"] or "无"))
        from widgets import kit as _k
        _k.info(self, "模块诊断", "\n".join(lines))

    def _spawn_rule_thread(self, p):
        try:
            if not self._thread_slots.acquire(blocking=False):
                self._pending_rules.discard(p.name)
                return False

            def work():
                try:
                    val = p.collect()
                except Exception:
                    val = "—"
                finally:
                    try:
                        self._thread_slots.release()
                    except Exception:
                        pass
                try:
                    self._rule_done.emit(p.name, val)
                except Exception:
                    pass
            t = threading.Thread(target=work, daemon=True)
            t.start()
            return True
        except Exception:
            self._pending_rules.discard(p.name)
            return False

    def _stop_chat(self, name):
        """停止按钮：中断指定对话模块的回复生成。
        - llm（API）：置位事件 + abort_stream 关闭响应，解除 worker 阻塞读
        - webchat：停止后端轮询，用已抓取内容结束
        """
        try:
            ev = self._chat_stop.get(name)
            if ev is not None:
                ev.set()
            for p in self._rules:
                if p.name == name:
                    try:
                        p.abort_stream()
                    except Exception:
                        pass
            # 立即回收 UI/调度态：即使 provider 不响应中止（worker 卡住），也不让
            # 该模块永久停更、行永远转圈（见气泡稳定性 M1/H4）。worker 真正结束时
            # _on_chat_done 因 name 已不在 _chat_pending 而安全跳过计数，不会减成负。
            self._chat_pending.pop(name, None)
            self._stream_acc.pop(name, None)
            self._think_acc.pop(name, None)
            panel = self._panel_for_provider(name)
            if panel is not None:
                try:
                    panel.set_thinking(False)
                except Exception:
                    pass
        except Exception:
            pass

    def _send_chat(self, provider, text, images=None):
        if provider is None:
            return
        name = provider.name
        panel = self._panel_for_provider(name)
        if panel is None:
            return
        # 防御纵深：同名对话已有回复在途时，拒绝并发第二个 worker（避免覆盖
        # _chat_stop[name]、流式交错）。正常路径已由 ChatPanel._send 守卫拦截。
        if self._chat_pending.get(name, 0) > 0:
            return
        self._chat_pending[name] = self._chat_pending.get(name, 0) + 1
        panel.set_thinking(True)
        self._last[name] = (time.monotonic(), "…")
        self._refresh()

        def work():
            reply = "（发送失败）"
            try:
                # 把面板里的历史对话一并传给大模型（最后一条是刚发送的当前消息）
                history = []
                try:
                    history = [{"role": m.get("role", "user"),
                                "text": str(m.get("text", ""))}
                               for m in panel._messages[:-1] if m.get("text")]
                except Exception:
                    pass
                # 全局线程槽位：排队等待，避免模块线程过多
                if self._thread_slots.acquire(timeout=30):
                    try:
                        src = provider._source()
                        if src.get("type") == "llm":
                            got = False
                            reply = ""
                            _tools_err = None
                            ev = threading.Event()
                            self._chat_stop[name] = ev
                            try:
                                self._chat_status.emit(name, "连接中…")
                                last_emit = 0.0   # worker 侧限频：避免数千信号淹没主线程队列
                                tool_names = src.get("tools") or []
                                if tool_names:
                                    _it = provider.chat_stream_tools(
                                        text, history, tool_names, stop_event=ev,
                                        on_tool=lambda tn, ta, tr, n=name:
                                        self._tool_step.emit(
                                            n, _tool_step_text(tn, ta, tr)))
                                else:
                                    _it = provider.chat_stream(
                                        text, history, stop_event=ev,
                                        images=images)
                                for chunk, reason in _it:
                                    got = True
                                    reply = chunk
                                    now = time.monotonic()
                                    if now - last_emit >= 0.08:
                                        if reason:
                                            self._chat_think.emit(name, reason)
                                        self._chat_status.emit(name, "接收中…")
                                        self._chat_chunk.emit(name, chunk)
                                        last_emit = now
                            except Exception as e:
                                got = False
                                _tools_err = e
                            finally:
                                self._chat_stop.pop(name, None)
                            self._chat_status.emit(name, "")
                            if ev.is_set():
                                # 用户主动停止：保留已生成内容并标记
                                reply = str(reply or "") + "（已停止）"
                            elif not got:
                                if tool_names:
                                    # 配置了工具却失败：显示真实错误，避免静默降级成
                                    # 无工具聊天（模型会假装执行过）
                                    reply = "（工具对话失败：%s）" % _tools_err
                                else:
                                    reply = provider.chat(text, history)   # 流式失败回退
                        else:
                            reply = provider.chat(text, history)
                    finally:
                        self._thread_slots.release()
                else:
                    reply = "（排队超时，请稍后再试）"
            except Exception:
                try:
                    reply = provider.chat(text, history)
                except Exception:
                    reply = "（发送失败）"
            self._chat_done.emit(name, reply)
        threading.Thread(target=work, daemon=True).start()

    def _on_chat_chunk(self, name, text):
        """流式增量：先累积，再按 80ms 批量刷新面板（长代码不会卡住 UI）。"""
        self._stream_acc[name] = text
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _on_chat_think(self, name, text):
        """思考过程增量：累积后随批量刷新一起显示。"""
        self._think_acc[name] = text
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream(self):
        """把累积的流式文本一次性刷到面板（限频，避免 UI 卡顿）。"""
        try:
            for name, text in list(self._stream_acc.items()):
                if name not in self._chat_pending:
                    continue   # 回复已完成/已取消：丢弃未刷完的旧累积
                panel = self._panel_for_provider(name)
                if panel is not None:
                    panel.append_stream(text)
            self._stream_acc.clear()
            for name, text in list(self._think_acc.items()):
                if name not in self._chat_pending:
                    continue
                panel = self._panel_for_provider(name)
                if panel is not None:
                    panel.set_thinking_text(text)
            self._think_acc.clear()
        finally:
            self._stream_timer.stop()

    def _on_chat_status(self, name, text):
        """连接/接收状态提示。"""
        try:
            panel = self._panel_for_provider(name)
            if panel is not None:
                panel.set_status(text)
        except Exception:
            pass

    def _on_tool_step(self, name, text):
        """工具执行步骤可视化：在对话面板里追加一行灰色步骤记录。"""
        try:
            panel = self._panel_for_provider(name)
            if panel is not None:
                panel.add_tool_step(text)
        except Exception:
            pass

    def _panel_for_provider(self, name):
        for it in self._inter_widgets:
            if it.get("chat") and it["provider"].name == name:
                return it["widget"]
        return None

    # ---------- 模块行右键菜单（刷新/启停/排序/删除） ----------

    _ROW_MENU_QSS = (
        "QMenu{background:#232a3a;color:#e8ecf5;border:1px solid "
        "rgba(255,255,255,40);padding:2px;}"
        "QMenu::item{padding:4px 16px;font-size:10px;}"
        "QMenu::item:selected{background:rgba(74,144,226,130);border-radius:3px;}"
        "QMenu::item:disabled{color:rgba(200,215,240,70);}")

    def contextMenuEvent(self, event):
        """模块行右键：立即刷新 / 禁用启用 / 上移 / 下移 / 删除模块。

        同时支持两种气泡：旧版自绘行（_rows/_row_y）与新版布局行（_row_widgets）。
        """
        try:
            idx = self._row_index_at(event.pos())
            if idx < 0:
                event.ignore()
                return
            prov = self._provider_for_row(idx)
            if prov is None:
                event.ignore()
                return
            event.accept()
            menu = QMenu(self)
            menu.setStyleSheet(_scaled_qss(self._ROW_MENU_QSS))
            has_rule = bool((prov.rule or {}).get("id"))
            act_edit = menu.addAction("编辑")
            act_edit.setEnabled(has_rule)
            act_edit.triggered.connect(
                lambda _=False, p=prov: self._edit_provider(p))
            act_refresh = menu.addAction("立即刷新")
            act_refresh.triggered.connect(
                lambda _=False, p=prov: self._force_refresh_provider(p))
            act_diag = menu.addAction("诊断信息")
            act_diag.triggered.connect(
                lambda _=False, p=prov: self._show_provider_diag(p))
            enabled = bool((prov.rule or {}).get("enabled", True))
            act_toggle = menu.addAction("禁用" if enabled else "启用")
            act_toggle.triggered.connect(
                lambda _=False, p=prov: self._toggle_provider(p))
            act_up = menu.addAction("上移")
            act_up.setEnabled(idx > 0)
            act_up.triggered.connect(
                lambda _=False, p=prov: self._move_provider(p, -1))
            act_down = menu.addAction("下移")
            act_down.setEnabled(idx < self._row_count() - 1)
            act_down.triggered.connect(
                lambda _=False, p=prov: self._move_provider(p, 1))
            with _menu_hold(self):
                menu.exec_(event.globalPos())
        except Exception:
            event.ignore()

    def _edit_provider(self, prov):
        """气泡右键"编辑"：打开模块编辑窗口，保存后实时更新气泡。"""
        try:
            pet = getattr(self, "pet", None)
            if pet is None:
                return
            fn = getattr(pet, "edit_rule_popup", None)
            if callable(fn):
                fn(prov.rule)
        except Exception:
            pass

    def _row_count(self):
        """当前可见行数（布局版用 _row_widgets，旧版用 _rows）。"""
        try:
            if getattr(self, "_LAYOUT", False) and hasattr(self, "_row_widgets"):
                return len([w for w in self._row_widgets if _w_is_alive(w)])
        except Exception:
            pass
        return len(self._rows)

    def _row_index_at(self, pos):
        """命中检测：返回光标下的行索引（布局版/旧版通用），未命中返回 -1。"""
        try:
            if getattr(self, "_LAYOUT", False) and hasattr(self, "_row_widgets"):
                content = getattr(self, "_content", self)
                base = content.pos() if content is not self else QPoint(0, 0)
                for i, wrap in enumerate(self._row_widgets):
                    if _w_is_alive(wrap):
                        # wrap 在 _content 里：气泡坐标 = content 偏移 + wrap 几何
                        r = wrap.geometry().translated(base)
                        if r.contains(pos):
                            return i
                return -1
        except Exception:
            pass
        # 旧版自绘行：按 y 坐标命中
        for i, (t, lines, row_h, widget) in enumerate(self._rows):
            top = self._row_y[i] if i < len(self._row_y) else 0
            if widget is not None:
                h = widget.height()
            else:
                h = row_h
            if top <= pos.y() < top + max(1, h):
                return i
        return -1

    def _provider_for_row(self, idx):
        """按行索引找对应提供者（规则优先，其次内置提供者）。"""
        try:
            rid = self._row_rule_ids[idx] if idx < len(self._row_rule_ids) else None
            if rid is not None:
                for p in self._rules:
                    if (p.rule.get("id") or "") == rid:
                        return p
            # 标题匹配：布局版/旧版分别从行数据取标题
            title = ""
            if getattr(self, "_LAYOUT", False) and hasattr(self, "_row_widgets"):
                rows_src = self._disp_rows if self._disp_rows else self._raw_rows
                if 0 <= idx < len(rows_src):
                    title = rows_src[idx][0]
            elif idx < len(self._rows):
                title = self._rows[idx][0]
            for p in self._visible_providers():
                if p.title == title or p.name == title:
                    return p
        except Exception:
            pass
        return None

    def _force_refresh_provider(self, prov):
        """立即重新取数：清掉缓存条目后刷新（规则线程/内置提供者通用）。"""
        try:
            name = getattr(prov, "name", "")
            if name:
                self._module_scheduler.reset(name)
            self._last.pop(prov.name, None)
            self._refresh()
        except Exception:
            pass

    def _apply_settings_mutation(self, mutator):
        """mutator(settings) -> bool；返回 True 时保存并热重载气泡规则。"""
        try:
            from pet_gravity import load_settings, save_settings
            st = load_settings()
            if not mutator(st):
                return
            save_settings(st)
            self.pet.settings.update(st)
            self.set_config(rules=st.get("status_rules", []))
        except Exception:
            pass

    def _toggle_provider(self, prov):
        rid = (prov.rule or {}).get("id")
        if not rid:
            return

        def m(st):
            for r in st.get("status_rules", []):
                if r.get("id") == rid:
                    r["enabled"] = not bool(r.get("enabled", True))
                    return True
            return False

        self._apply_settings_mutation(m)

    def _move_provider(self, prov, delta):
        rid = (prov.rule or {}).get("id")
        if not rid:
            return

        def m(st):
            rules = st.get("status_rules", [])
            for i, r in enumerate(rules):
                if r.get("id") == rid:
                    j = i + delta
                    if 0 <= j < len(rules):
                        rules[i], rules[j] = rules[j], rules[i]
                        return True
                    return False
            return False

        self._apply_settings_mutation(m)

    def _delete_provider(self, prov):
        rid = (prov.rule or {}).get("id")
        if not rid:
            return
        # 内置模块同样可删除，删除前确认
        from widgets import kit as _k
        if not _k.confirm(
                self, "删除模块", "确定删除模块「%s」？\n删除后不可恢复。"
                % ((prov.rule or {}).get("name", "模块")), danger=True):
            return

        def m(st):
            rules = st.get("status_rules", [])
            keep = [r for r in rules if r.get("id") != rid]
            if len(keep) == len(rules):
                return False
            st["status_rules"] = keep
            # 内置模块必须记入 hidden_builtins，否则 load_settings 会重新合并回来
            hidden = list(set(st.get("hidden_builtins") or []))
            if rid:
                hidden.append(rid)
            st["hidden_builtins"] = hidden
            return True

        self._apply_settings_mutation(m)

    def _widget_for_rule(self, name):
        """按规则名找对应组件（对话面板/番茄卡片/自定义组件通用）。"""
        for it in self._inter_widgets:
            if it["provider"].name == name and it.get("widget") is not None:
                return it["widget"]
        return None

    def _on_rule_done(self, name, val):
        self._pending_rules.discard(name)
        self._module_scheduler.pending.discard(name)
        old = self._last.get(name)
        old_val = old[1] if old else None
        self._last[name] = (time.monotonic(), val)
        # 自定义组件：取数完成后刷新组件显示（对话面板由 add_message 自行刷新）
        w = self._widget_for_rule(name)
        if w is not None and not isinstance(w, ChatPanel):
            try:
                w.render(getattr(w, "state", {}), val)
            except Exception:
                pass
        # 规则取数失败：弹出错误提示（每种错误只提示一次，恢复后重置）
        p = next((r for r in self._rules if r.name == name), None)
        if p is not None:
            err = getattr(p, '_last_error', '') or ''
            self._module_scheduler.finish(name, bool(err), time.monotonic())
            if err:
                if name not in self._err_notified:
                    self._err_notified.add(name)
                    self.show_error_popup("模块出错", "%s：%s" % (p.title, err[:120]))
            else:
                self._err_notified.discard(name)
        if old_val != val:
            self._maybe_auto_popup(name)
        if self.isVisible():
            # 弹出后动态更新该规则行
            self._refresh()

    def _on_tool_reminder(self, text):
        """LLM 工具（remind）触发的定时提醒：主线程弹气泡提示。"""
        try:
            self.show_error_popup("⏰ 提醒", str(text))
        except Exception:
            pass

    def _tool_audit(self, action, payload):
        """桥命令审计：写 %TEMP%\\oi_pet_tools.log，与 status_monitor._tool_log 同文件。"""
        try:
            with open(os.path.join(os.environ.get("TEMP", "."),
                                   "oi_pet_tools.log"),
                      "a", encoding="utf-8") as f:
                f.write("[%s] [bridge:%s] %s\n"
                        % (time.strftime("%H:%M:%S"), action,
                           json.dumps(payload or {}, ensure_ascii=False)[:160]))
        except Exception:
            pass

    def _on_tool_command(self, action, payload):
        """LLM 工具的主线程命令：同步内存设置 + 重载模块/按钮 + 控制桌宠。"""
        try:
            if action == "rules_reload":
                from pet_gravity import load_settings
                st = load_settings()
                self.pet.settings.update(st)
                self.set_config(rules=st.get("status_rules", []))
                self.pet.sync_settings_dialog()   # 设置窗开着就一起同步
                self._tool_audit("rules_reload", {"rules": len(st.get("status_rules", []))})
            elif action == "buttons_reload":
                from pet_gravity import load_settings
                st = load_settings()
                self.pet.settings.update(st)
                # 径向菜单刷新：任何状态下先无动画收起（完整清空按钮状态），
                # 若菜单原本是展开的，立即用新列表重新展开——用户立刻看到变化，
                # 不需要手动关开；收起态则下次打开自然是最新列表
                m = getattr(self.pet, "radial_menu", None)
                if m is not None:
                    was_visible = False
                    try:
                        was_visible = bool(m.is_visible_state)
                    except Exception:
                        pass
                    try:
                        m.hide_menu(animate=False)
                    except Exception:
                        pass
                    if was_visible:
                        try:
                            m.show_menu(list(
                                self.pet.settings.get("slot_shortcuts", []))[:8])
                        except Exception:
                            pass
                self.pet.sync_settings_dialog()   # 设置窗开着就一起同步
                self._tool_audit("buttons_reload", {})
            elif action == "todo_reload":
                # AI 工具改了 todo_data.json：刷新所有嵌入的待办组件
                for it in self._inter_widgets:
                    w = it.get("widget")
                    if w is not None and getattr(w, "_ui_name", "") == "todo":
                        try:
                            w.reload()
                        except Exception:
                            pass
                self._tool_audit("todo_reload", {})
            elif action == "canvas_apply":
                # AI 绘画：把图元指令应用到画布组件（必要时展开）
                cmds = (payload or {}).get("cmds", [])
                done = self._canvas_apply(cmds)
                self._tool_audit("canvas_apply", {"n": len(cmds), "done": done})
            elif action == "perler_apply":
                # AI 拼豆绘画：把像素坐标应用到拼豆组件（必要时展开）
                cells = (payload or {}).get("cells", [])
                done = self._perler_apply(cells)
                self._tool_audit("perler_apply", {"n": len(cells), "done": done})
            elif action == "pet_setting":
                key = (payload or {}).get("key")
                value = (payload or {}).get("value")
                if key:
                    try:
                        self.pet.settings[key] = value
                        self.pet._apply_pet_settings()
                        # 设置窗开着的话一起同步：否则窗里显示的是旧值，
                        # 点确定还会把 AI 刚改的覆盖回去
                        self.pet.sync_settings_dialog()
                    except Exception:
                        pass
                    self._tool_audit("pet_setting", {"key": key, "value": value})
            elif action == "pet_control":
                a = (payload or {}).get("action")
                if a == "hide":
                    self.pet.hide()
                elif a == "show":
                    self.pet.show()
                elif a == "move":
                    self.pet.move(int(payload.get("x", 0)),
                                  int(payload.get("y", 0)))
                self._tool_audit("pet_control", payload or {})
        except Exception as e:
            # 失败不再静默：写错误日志，便于排查"工具执行了但界面没变化"
            try:
                import traceback as _tb
                with open(os.path.join(os.environ.get("TEMP", "."),
                                       "oi_pet_error.log"),
                          "a", encoding="utf-8") as f:
                    f.write("\n[tool_command %s] %s\n%s\n"
                            % (action, e, _tb.format_exc()))
            except Exception:
                pass

    def _canvas_apply(self, cmds):
        """把 AI 生成的图元指令应用到画布组件；返回是否找到并应用。"""
        try:
            for it in self._inter_widgets:
                w = it.get("widget")
                if w is not None and getattr(w, "_ui_name", "") == "canvas":
                    try:
                        w._ensure_expanded()
                        n = w._apply_commands(cmds or [])
                        w._schedule_save()
                        w._status.setText("AI 完成：%d 个图元" % n)
                        return True
                    except Exception:
                        return False
            # 规则里存在 canvas 但组件未加载：触发布局重建后重试
            for p in self._rules:
                if (p._source().get("ui") == "canvas"
                        and p.rule.get("embed", True)):
                    self._rebuild_interactive()
                    for it in self._inter_widgets:
                        w = it.get("widget")
                        if w is not None and getattr(w, "_ui_name", "") == "canvas":
                            try:
                                w._ensure_expanded()
                                n = w._apply_commands(cmds or [])
                                w._schedule_save()
                                w._status.setText("AI 完成：%d 个图元" % n)
                                return True
                            except Exception:
                                return False
            return False
        except Exception:
            return False

    def _perler_apply(self, cells):
        """把 AI 生成的像素坐标应用到拼豆组件；返回是否找到并应用。"""
        try:
            for it in self._inter_widgets:
                w = it.get("widget")
                if w is not None and getattr(w, "_ui_name", "") == "perler":
                    try:
                        if w._collapsed:
                            w._toggle_fold()
                        n = w.apply_cells(cells or [])
                        w._status.setText("AI 完成：%d 个像素" % n)
                        return True
                    except Exception:
                        return False
            for p in self._rules:
                if (p._source().get("ui") == "perler"
                        and p.rule.get("embed", True)):
                    self._rebuild_interactive()
                    for it in self._inter_widgets:
                        w = it.get("widget")
                        if w is not None and getattr(w, "_ui_name", "") == "perler":
                            try:
                                if w._collapsed:
                                    w._toggle_fold()
                                n = w.apply_cells(cells or [])
                                w._status.setText("AI 完成：%d 个像素" % n)
                                return True
                            except Exception:
                                return False
            return False
        except Exception:
            return False

    def _on_chat_done(self, name, val):
        """对话回复完成：只追加聊天记录，不参与模块定时取数。"""
        if name in self._chat_pending:
            self._chat_pending[name] -= 1
            if self._chat_pending[name] <= 0:
                self._chat_pending.pop(name, None)
        old = self._last.get(name)
        old_val = old[1] if old else None
        self._last[name] = (time.monotonic(), val)
        # 自定义组件：取数完成后刷新组件显示（对话面板由 add_message 自行刷新）
        w = self._widget_for_rule(name)
        if w is not None and not isinstance(w, ChatPanel):
            try:
                w.render(getattr(w, "state", {}), val)
            except Exception:
                pass
        panel = self._panel_for_provider(name)
        if panel is not None:
            if name not in self._chat_pending:
                panel.set_thinking(False)
            panel.end_stream(val)
        if old_val != val:
            self._maybe_auto_popup(name)
        if self.isVisible():
            self._refresh()

    def show_error_popup(self, title, msg):
        """显示错误提示弹窗（非阻塞，数秒后自动消失）。"""
        try:
            self._err_popup.show_error(title, msg)
        except Exception:
            pass

    def _maybe_auto_popup(self, name):
        """规则内容变化且标记"自动弹出"时：独立浮出小气泡（类似情绪气泡）。"""
        p = next((r for r in self._rules if r.name == name), None)
        if not p or not p.popup:
            return
        if not getattr(self.pet, "bubble_enabled", True):
            return
        if not self.pet.isVisible() or self.pet.is_fullscreen:
            return
        val = self._last.get(name, (0, ""))[1] or p.rule.get("fallback", "")
        dur = max(1, int(p.rule.get("popup_duration", 3) or 3)) * 1000
        try:
            pb = self._popup_pool.pop(name, None)
            if pb is None or not _w_is_alive(pb):
                pb = _st_monitor("PopupBubble")(self.pet)
            self._popup_pool[name] = pb   # 移到末尾（最近使用）
            self._prune_popup_pool()
            pb.show_text(val, dur)
        except Exception:
            pass

    def _prune_popup_pool(self):
        """弹出气泡池最多保留 8 个，超出时回收最久未用的。"""
        try:
            while len(self._popup_pool) > 8:
                oldest = next(iter(self._popup_pool))
                pb = self._popup_pool.pop(oldest)
                try:
                    pb.hide()
                    pb.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass

    def show_near(self):
        if self.isVisible():
            # 已显示（含展开动画中）：只刷新内容，不重复弹出
            self._refresh()
            return
        # 菜单展开时也显示气泡（位置会上移到菜单上方）
        self._do_show()

    def _do_show(self):
        self._awaiting = False
        self._fade_active = False
        self._fade_timer.stop()
        # 清理残留动画状态，保证从"一条线向上展开"
        self._settle_active = False
        self._anim_timer.stop()
        self._anim_dir = 0
        self._refresh()
        self._relayout()
        tx, ty = self._place()
        self._full_h = self.height()
        # 以目标位置为基准锚定底边：展开动画与平滑移动不会互相拉扯
        self._anim_bottom = ty + self._full_h
        # 弹出前先精确落到目标位：否则窗口会先出现在上一次的旧位置（可能在屏幕
        # 另一侧/边缘），再被 _smooth_move_to 从远处挪过来，出现"飞过来/跳过来"。
        self.move(tx, ty)
        self._mv_from = None
        self._mv_prev_target = (tx, ty)
        self.show()
        self.raise_()
        # 显示后立即展示交互组件（对话面板/番茄卡片），避免与展开动画冲突
        self._show_widgets()
        # 从桌宠上方的一条线开始，向上展开成气泡框
        self.setWindowOpacity(0.0)
        self._apply_height(2)
        self._play_height(True)
        # 鼠标在桌宠上悬停时不消失；规则自动弹出则 3 秒后收起
        if not getattr(self.pet, "underMouse", lambda: False)():
            self._auto_hide_timer.start(3000)
        self._maybe_ui_timer()

    def _show_widgets(self):
        for w, x, y, ww, wh in self._widget_geos:
            try:
                w.setGeometry(x, y, ww, wh)
                w.show()
                w.raise_()
            except Exception:
                pass

    def _place(self):
        if getattr(self, "_fold_locked", False):
            return (self.x(), self.y())   # 折叠动画期间保持原位，不做整框移动
        # 动画/补位期间用目标高度计算位置，避免收起时随实时高度往下漂
        hh = self._full_h if (self._anim_dir or self._settle_active) else self.height()
        # 用"稳定侧"计算目标：resize / 移动动画中途实时几何被破坏时，不会误判
        # 所在侧，从而避免气泡先朝桌宠方向绕路、再折返去目的地。
        side = self._side_hint or self._current_side()
        tx, ty, mode = self._compute_target_geom(hh, side)
        self._mv_target = (tx, ty)
        new_side = {"bottom": "above", "top": "below",
                    "left": "left", "right": "right"}.get(mode, side)
        if getattr(self.pet, 'is_dragging', False):
            # 拖动桌宠：统一"指数平滑跟随"——每帧向目标逼近剩余距离的一部分，
            # 既跟手又不跳。之前是"同侧立即贴位 / 异侧 0.3s 动画"混用，快速移动、
            # 方向来回切换时会把气泡从动画中途直接瞬移（"概率一帧跳过去"）。
            if self._settle_active:
                self._settle_active = False
                self._anim_timer.stop()
                try:
                    self.resize(self._settle_w2, self._settle_h2)
                except Exception:
                    pass
            dx = tx - self.x()
            dy = ty - self.y()
            if abs(dx) > 1 or abs(dy) > 1:
                a = 0.5   # 每帧逼近剩余 50%：约 2-3 帧到位，快速且平滑
                self.move(int(self.x() + dx * a), int(self.y() + dy * a))
            else:
                self.move(tx, ty)
            self._mv_from = None
            self._mv_timer.stop()
        elif (self._anim_dir or self._settle_active):
            # 动画/补位期间由动画自身控制几何，不启动位置缓动，避免互相拉扯
            self._mv_timer.stop()
        else:
            self._smooth_move_to(tx, ty)
        # 到位后更新稳定侧；移动过程中（_mv_from 非 None）保持不变，目标不抖动
        if self._mv_from is None:
            self._side_hint = new_side
        self._place_popup()
        return tx, ty

    def _smooth_move_to(self, tx, ty):
        """帧驱动 0.3s ease-out 补位：起点 → 目标，移动过程可见。
        目标变化（含菜单快速展开/收起导致的方向反转）时，从当前位置重新起动画，
        避免沿用旧起点导致"一帧切过去"。"""
        if (self.x(), self.y()) != (tx, ty):
            if self._mv_from is None or getattr(self, "_mv_prev_target", None) != (tx, ty):
                # 首次移动，或目标变化：从当前位置重新记录起点
                self._mv_from = (self.x(), self.y())
                self._mv_t0 = time.monotonic()
            self._mv_prev_target = (tx, ty)
            k = min(1.0, (time.monotonic() - self._mv_t0) / max(0.05, self._mv_dur))
            e = 1.0 - (1.0 - k) ** 3   # ease-out：开头快、结尾缓
            fx, fy = self._mv_from
            self.move(int(fx + (tx - fx) * e), int(fy + (ty - fy) * e))
            if k >= 1.0:
                self._mv_from = None   # 到位后结束本次移动
        else:
            self._mv_from = None   # 已在目标位，清理残留起点

    def _current_side(self):
        """气泡当前在桌宠的哪一侧（above/below/left/right），按中心位置比较。"""
        try:
            pc = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, 0))
            cy = pc.y() + self.pet.height() // 2
            bc = self.y() + self.height() // 2
            if bc < cy - 1:
                return "above"
            if bc > cy + 1:
                return "below"
            # 侧方：按水平位置区分左右（用于拖动时左右切换也要有过渡）
            if self.x() + self.width() // 2 < pc.x():
                return "left"
            return "right"
        except Exception:
            return "above"

    def _nook_dock(self, hh, pc, g, w, clear, place_left):
        """环绕桌宠：把桌宠嵌进"短列下方那块空缺"里，整体外接矩形窄掉一整列。

        分列之后各列高度不同，靠桌宠那一侧的列如果明显更矮，它下方就是一块
        空的凹口——而且 `_apply_col_mask` 早就把那块从窗口遮罩里挖掉了，
        所以桌宠摆进去照样看得见、点得到，不是被气泡压住。

        返回 (x, y) 或 None（放不进去就让调用方按老办法摆在旁边）。
        """
        panels = getattr(self, "_col_panels", None) or []
        if len(panels) < 2:
            return None
        col_w = int(self._FIX_W)
        # 靠桌宠那一侧的列：气泡在左边时是最后一列，在右边时是第一列
        near = panels[-1] if place_left else panels[0]
        near_h = int(near[3])
        full = max(int(p[3]) for p in panels)
        pet_h = self.pet.height()
        pet_top = pc.y()
        gap = _bs(6)
        nook_h = full - near_h
        if nook_h < pet_h + gap * 2:
            return None                  # 凹口装不下桌宠
        # 让凹口顶边正好落在桌宠上边之上一点，桌宠就坐进凹口里
        y = pet_top - gap - near_h
        y = max(g.top(), min(y, g.bottom() - hh))
        # 夹到屏幕内之后要复核：凹口必须真的整个罩住桌宠，否则会压住它
        if not (y + near_h <= pet_top and y + full >= pet_top + pet_h):
            return None
        if place_left:
            # 往右挪一整列：那一列横向与桌宠重叠，但桌宠落在它下方的凹口里
            x = pc.x() - clear - w + col_w
        else:
            x = pc.x() + clear - col_w
        x = max(g.left(), min(int(x), g.right() - w))
        # 夹完再复核横向：靠桌宠那一列必须真的盖过桌宠所在的横向区间，
        # 否则就是白挪一列（桌宠悬在气泡外面，整体反而更宽）
        if place_left:
            near_x0, near_x1 = x + w - col_w, x + w
        else:
            near_x0, near_x1 = x, x + col_w
        if not (near_x0 <= pc.x() and pc.x() + 0 <= near_x1):
            return None
        return int(x), int(y)

    def _side_geom(self, hh, pc, g, menu_r=0, prefer=None):
        """上下都放不下时：放到桌宠左右侧（菜单展开时同时让开菜单盘），
        垂直居中于桌宠，不挡桌宠与菜单。prefer 为 "left"/"right" 时，仅当该侧
        仍有足够空间才保持（避免左右来回跳），否则按屏幕空间选宽松一侧。

        多列且靠桌宠那列明显更矮时，先试「把桌宠嵌进凹口」（见 _nook_dock）——
        桌宠和气泡当成一个整体看，这样整体占的地方最小。
        """
        w = self.width()
        pet_cx = pc.x()
        pet_cy = pc.y() + self.pet.height() // 2
        clear = max(self.pet.width() // 2, menu_r)   # 至少让开桌宠，菜单展开时让开菜单
        place_left = (pet_cx - g.left() >= g.right() - pet_cx)
        # prefer 侧仍有完整空间时保持，否则跟随空间判断（合理切换）
        if prefer == "left" and pet_cx - clear - w - 8 >= g.left():
            place_left = True
        elif prefer == "right" and pet_cx + clear + 8 + w <= g.right():
            place_left = False
        if menu_r <= 0:      # 菜单展开时不玩嵌套，菜单盘会压到气泡上
            dock = self._nook_dock(hh, pc, g, w, clear, place_left)
            if dock is not None:
                return dock[0], dock[1], ("left" if place_left else "right")
        if place_left:
            x = max(g.left(), pet_cx - clear - w - 8)
            mode = "left"
        else:
            x = min(g.right() - w, pet_cx + clear + 8)
            mode = "right"
        y = max(g.top(), min(pet_cy - hh // 2, g.bottom() - hh))
        return int(x), int(y), mode

    def _compute_target_geom(self, hh=None, side_hint=None):
        """计算气泡目标几何 (x, y, 锚点模式)。_place 与折叠动画共用同一套
        定位逻辑；side_hint 让气泡保持在当前一侧，避免折叠/刷新时来回跳。
        上下放不下时移到左右侧，不挡桌宠。"""
        if hh is None:
            hh = self.height()
        pc = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, 0))
        w = self.width()
        scr = QApplication.screenAt(self.pet.mapToGlobal(self.pet.rect().center())) \
            or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = pc.x() - w // 2
        menu = self.pet.radial_menu
        mode = "bottom"
        # 用稳定半径 _sector_outer_full（不随展开动画缩放），避免空菜单展开时气泡目标来回跳
        _so = menu._sector_outer_full() if hasattr(menu, "_sector_outer_full") else menu._sector_outer()
        if menu.is_visible_state and _so > 0:
            # 菜单展开：优先菜单盘上方，放不下则菜单盘下方避让，再不行左右
            cy = pc.y() + self.pet.height() // 2
            y = cy - _so - hh - 8
            if y < g.top():
                y2 = cy + _so + 8
                if y2 + hh <= g.bottom():
                    mode = "top"
                    y = y2
                else:
                    return self._side_geom(hh, pc, g, _so)
        else:
            # 上方 / 下方 / 左右：优先保持当前侧，避免来回跳（菜单未展开，menu_r=0）
            y_above = pc.y() - hh - 6
            y_below = pc.y() + self.pet.height() + 6
            fits_above = y_above >= g.top() and y_above + hh <= g.bottom()
            fits_below = y_below >= g.top() and y_below + hh <= g.bottom()
            if side_hint == "below" and fits_below:
                y = y_below
                mode = "top"
            elif side_hint == "above" and fits_above:
                y = y_above
                mode = "bottom"
            elif side_hint in ("left", "right"):
                return self._side_geom(hh, pc, g, 0, prefer=side_hint)
            elif fits_above:
                y = y_above
                mode = "bottom"
            elif fits_below:
                y = y_below
                mode = "top"
            else:
                return self._side_geom(hh, pc, g, 0)
        y = min(y, max(g.top(), g.bottom() - hh))   # 屏幕内收
        x = max(g.left(), min(int(x), g.right() - w))
        return int(x), int(y), mode

    def _place_popup(self):
        """所有独立弹出气泡跟随桌宠移动并自动避让。"""
        try:
            _st_monitor("_reflow_popups")(self.pet)
        except Exception:
            pass

    def _mv_tick(self):
        """侧切换 0.3s 移动动画：起点 → 目标线性+ease-out 插值，过程可见。"""
        if self._anim_dir or self._settle_active:
            return
        if getattr(self, "_fold_locked", False):
            return   # 折叠动画期间几何由折叠控制，位置缓动不参与
        tx, ty = self._mv_target
        if self._mv_from is None:
            # 未在动画中：直接到位并停止
            self._mv_timer.stop()
            return
        fx, fy = self._mv_from
        k = (time.monotonic() - self._mv_t0) / max(0.05, self._mv_dur)
        if k >= 1.0:
            self.move(tx, ty)
            self._mv_from = None
            self._mv_timer.stop()
            return
        # ease-out：开头快、结尾缓，0.3s 内可见滑动
        e = 1.0 - (1.0 - k) ** 3
        nx = fx + (tx - fx) * e
        ny = fy + (ty - fy) * e
        self.move(int(nx), int(ny))

    def _on_widget_resize(self, widget):
        """组件自己改了高度时的回调。基类没有行折叠动画，直接重排。"""
        self._relayout()

    def sizeHint(self):
        return QSize(self.width(), self.height())

    def _render_cache(self):
        """把气泡内容渲染到缓存位图：弹出动画期间每帧只做贴图，避免重绘卡顿"""
        w = self.width()
        h = self._full_h
        if w <= 1 or h <= 1:
            self._cached_pm = None
            return
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(28, 32, 44, 205))
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 6, 6)
        self._link_hits = []
        self._rendered_scroll = False   # 本次渲染是否存在滚动/加载动画（供定时器防冻结）
        fm = QFontMetrics(self._font)
        y = self._HEAD_H + 2
        link_font = QFont(self._font)
        link_font.setUnderline(True)
        now_m = time.monotonic()
        title_x = 11   # 给左侧拖拽手柄留位
        value_left = title_x + self._title_w + 6
        avail = w - 8 - value_left
        for i, (title, lines, row_h, widget) in enumerate(self._rows):
            if widget is not None:
                # 组件行标题栏（对话面板自带标题栏，不重复绘制）
                if (_w_is_alive(widget) and title
                        and not isinstance(widget, ChatPanel)):
                    ty = int(self._row_y[i] + self._drag_offset(i))
                    fm = QFontMetrics(self._font)
                    tt = fm.elidedText(str(title), Qt.ElideRight, self._title_w)
                    p.setPen(QColor(150, 170, 205))
                    p.setFont(self._font)
                    p.drawText(QRect(title_x, ty, self._title_w,
                                     self._ROW_TITLE_H),
                               Qt.AlignLeft | Qt.AlignVCenter, tt)
                # 左侧拖拽手柄（交互组件也显示，位于其左边缘）
                if _w_is_alive(widget):
                    hy = self._row_y[i] + self._drag_offset(i) + 8
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(140, 155, 185, 170))
                    for k in (-2.0, 0.0, 2.0):
                        p.drawEllipse(QPointF(5.0, hy + k), 1.2, 1.2)
                # 交互组件占用独立高度：后续行要跳过它，避免重叠
                wh = widget.current_height() if isinstance(widget, ChatPanel) else widget.height()
                y += wh + 2 + (self._ROW_TITLE_H
                               if (title and not isinstance(widget, ChatPanel))
                               else 0)
                continue
            if row_h <= 0:
                continue
            draw_y = self._row_y[i] if i < len(self._row_y) else y
            draw_y += self._drag_offset(i)
            draw_y = int(draw_y)
            # 模块卡片：浅色底 + 细边框 + 圆角，区分模块与气泡整体
            p.setPen(QPen(QColor(255, 255, 255, 42), 1))
            p.setBrush(QColor(255, 255, 255, 11))
            p.drawRoundedRect(QRectF(3, draw_y, w - 6, self._ROW_H), 3, 3)
            # 左侧拖拽手柄：竖排三点
            hx = 5.0
            hy = draw_y + self._ROW_H / 2.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(140, 155, 185, 170))
            for k in (-2.0, 0.0, 2.0):
                p.drawEllipse(QPointF(hx, hy + k), 1.2, 1.2)
            p.setFont(self._font)
            p.setPen(QColor(150, 170, 205))
            # 标题过长时省略显示，避免被裁断
            title_text = fm.elidedText(title, Qt.ElideRight, self._title_w)
            p.drawText(QRect(title_x, draw_y, self._title_w, self._ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, title_text)
            if title in self._pending_titles:
                # 思考/输出中：动态加载图标（旋转小圆弧）
                self._rendered_scroll = True
                r = 5
                cx = w - 8 - r
                cy = draw_y + self._ROW_H // 2
                pen = QPen(QColor(170, 190, 220), 2)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2),
                          int(((now_m * 4.0) % 1.0) * 360 * 16), int(110 * 16))
                y += self._ROW_H
                continue
            line = lines[0] if lines else ()
            prov = self._agent_map.get(title)
            if prov is not None and line:
                line = tuple(list(line) + [("  [审批]", ("approve", prov))])
            line_w = sum(fm.horizontalAdvance(t) for t, u in line)
            if line_w <= avail:
                # 内容不超宽：右对齐单行显示
                xcur = w - 8 - line_w
                self._draw_line_segs(p, fm, link_font, line, xcur, draw_y)
            else:
                # 超宽：单行滚动显示（字幕），不注册点击区域
                self._rendered_scroll = True
                speed = 75.0
                gap = 24
                span = line_w + avail + gap
                off = (now_m * speed) % span
                xcur = value_left + avail - off
                if xcur + line_w < value_left:
                    xcur += span
                p.save()
                p.setClipRect(QRect(value_left, draw_y - 1, avail + 1, self._ROW_H + 2))
                self._draw_line_segs(p, fm, link_font, line, xcur, draw_y, register=False)
                p.restore()
            y += self._ROW_H
        # 拖拽排序：目标插入位置高亮线
        if self._drag is not None and self._drag["target"] != self._drag["row"]:
            d = self._drag
            tgt = d["target"]
            if 0 <= tgt < len(self._rows):
                row = self._rows[tgt]
                if row[3] is not None and _w_is_alive(row[3]):
                    ext = row[3].height() if not isinstance(row[3], ChatPanel) else row[3].current_height()
                else:
                    ext = row[2]
                y_line = (self._row_y[tgt] + self._drag_offset(tgt)
                          + (ext if tgt > d["row"] else 0))
                p.setPen(QPen(QColor(120, 200, 255, 230), 2))
                p.drawLine(6, int(y_line), w - 6, int(y_line))
        p.end()
        self._cached_pm = pm

    def _draw_line_segs(self, p, fm, link_font, line, xcur, y, register=True):
        """绘制一行分段内容（链接下划线，可点击）"""
        xcur = int(xcur)
        for text, url in line:
            if url:
                p.setFont(link_font)
                p.setPen(QColor(120, 200, 255))
            else:
                p.setFont(self._font)
                p.setPen(QColor(235, 240, 248))
            tw = fm.horizontalAdvance(text)
            p.drawText(QRect(xcur, y, tw, self._ROW_H), Qt.AlignLeft | Qt.AlignVCenter, text)
            if url and register:
                self._link_hits.append((QRect(xcur, y, tw, self._ROW_H), url))
            xcur += tw

    def _ui_tick(self):
        """加载图标/滚动字幕逐帧动画"""
        try:
            if self.isVisible():
                self._render_cache()
                self.update()
                # 没有任何行需要动画时自停，避免动画残留
                if not (self._pending_titles or self._scroll_titles or self._rendered_scroll):
                    self._ui_timer.stop()
        except Exception:
            pass

    def _maybe_ui_timer(self):
        need = self.isVisible() and (
            bool(self._pending_titles) or bool(self._scroll_titles)
            or getattr(self, '_rendered_scroll', False))
        if need and not self._ui_timer.isActive():
            self._ui_timer.start(40)
        elif not need and self._ui_timer.isActive():
            self._ui_timer.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        if self._cached_pm is not None and not self._cached_pm.isNull():
            # 贴缓存内容（窗口高度裁剪由 Qt 完成，动画平滑）
            p.drawPixmap(0, 0, self._cached_pm)
            return
        # 回退：仅绘制背景
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(28, 32, 44, 205))
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            for rect, payload in self._link_hits:
                if rect.contains(event.pos()):
                    if isinstance(payload, tuple) and payload[0] == "approve":
                        try:
                            payload[1].approve()
                            self._refresh()
                        except Exception:
                            pass
                    elif isinstance(payload, str):
                        import webbrowser
                        webbrowser.open(payload)
                    event.accept()
                    return
            row = self._find_handle_row(event.pos())
            if row >= 0:
                self._drag = {"row": row,
                              "grab": event.pos().y() - self._row_y[row],
                              "mouse": event.pos().y(),
                              "target": row}
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self._drag["mouse"] = event.pos().y()
            # 以鼠标位置为插入锚点：与抓取点/面板高度无关，拖高大的对话面板也能精准插到边缘
            dragged_center = event.pos().y()
            target = self._drag["row"]
            for i in range(len(self._rows)):
                if i == self._drag["row"]:
                    continue
                row = self._rows[i]
                if row[3] is not None:
                    # 交互组件行：按其高度参与插入判定
                    if not _w_is_alive(row[3]):
                        continue
                    y0 = self._row_y[i]
                    ext = (row[3].current_height() if isinstance(row[3], ChatPanel)
                           else row[3].height())
                else:
                    y0 = self._row_y[i]
                    ext = row[2]
                if y0 <= dragged_center < y0 + ext:
                    target = i
                    break
            self._drag["target"] = target
            # 拖拽期间让交互组件跟随偏移
            for i, (t, ln, rh, w) in enumerate(self._rows):
                if w is not None and _w_is_alive(w) and i < len(self._row_y):
                    off = self._drag_offset(i)
                    wh = w.current_height() if isinstance(w, ChatPanel) else w.height()
                    w.setGeometry(16, int(self._row_y[i] + 2 + off),
                                  self.width() - 22, wh)
            self._render_cache()
            self.update()
            event.accept()
            return
        over_link = any(r.contains(event.pos()) for r, u in self._link_hits)
        if self._find_handle_row(event.pos()) >= 0:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.PointingHandCursor if over_link else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        d = self._drag
        if d is not None:
            self._drag = None
            try:
                if d["row"] != d["target"]:
                    self._commit_reorder(d["row"], d["target"])
            except Exception:
                pass
            self._render_cache()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _find_handle_row(self, pos):
        """命中左侧拖拽手柄的行（只限可绘制行）。"""
        if pos.x() > 12 or pos.y() < 0 or not self._row_y:
            return -1
        for i, (t, lines, row_h, w) in enumerate(self._rows):
            if w is not None:
                if not _w_is_alive(w):
                    continue
                y0 = self._row_y[i] + self._drag_offset(i)
                wh = w.current_height() if isinstance(w, ChatPanel) else w.height()
                if y0 <= pos.y() < y0 + wh:
                    return i
                continue
            if row_h <= 0:
                continue
            y0 = self._row_y[i] + self._drag_offset(i)
            if y0 <= pos.y() < y0 + self._ROW_H:
                return i
        return -1

    def _drag_offset(self, i):
        """拖拽时行的纵向偏移：只有拖拽行跟随鼠标，其余行保持原位，避免重排跳动。"""
        if not self._drag or not self._rows or i >= len(self._rows):
            return 0.0
        d = self._drag
        if i == d["row"]:
            return d["mouse"] - d["grab"] - self._row_y[i]
        return 0.0

    def _commit_reorder(self, src, dst):
        """拖拽结束：把模块移动到目标位置并保存。"""
        try:
            ids = [r.get("id") for r in getattr(self, '_all_rule_cfgs', [])]
            sid = self._row_rule_ids[src] if src < len(self._row_rule_ids) else None
            did = self._row_rule_ids[dst] if dst < len(self._row_rule_ids) else None
            if sid is None or did is None or sid not in ids or did not in ids:
                return
            si, di = ids.index(sid), ids.index(did)
            rules = list(self._all_rule_cfgs)
            r = rules.pop(si)
            rules.insert(di, r)
            try:
                import pet_gravity
                self.pet.settings["status_rules"] = rules
                pet_gravity.save_settings(self.pet.settings)
            except Exception:
                pass
            self.set_config(rules=rules)
        except Exception:
            pass
