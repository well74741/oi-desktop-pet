# -*- coding: utf-8 -*-
"""布局版气泡（当前唯一启用版本）：用 Qt 布局引擎排布内容。

桌宠实际只创建 StatusBubbleLayout（pet_gravity 里无条件实例化）；基类
StatusBubble 的旧手绘呈现已退役（不可达）。此版本与基类共享取数/弹出/
组件/聊天/工具引擎，只替换内容排布：
- 所有文字行、交互组件都由 QVBoxLayout 排布，Qt 负责几何与重排；
- 拖拽排序实时移动行（Qt 布局即时重排，不再有跳变）；
- 长文本仍支持横向滚动字幕，链接点击照常。

已知差异（对比用）：H5 卡片暂以覆盖层方式显示；加载中显示为文字而非旋转图标。
"""
import html
import time

from PyQt5.QtCore import Qt, QPoint, QRect, QRectF, QSize, QTimer
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPen,
                         QContextMenuEvent)
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

from bubble_ui import (StatusBubble, ChatPanel, _w_is_alive, _split_links,
                       _widget_height)


def _font7():
    from widgets import kit as _kit
    f = QFont("Microsoft YaHei")
    f.setPointSizeF(11.25 * _kit.bubble_k())
    return f


def _font_h():
    """一行基础字号文字真正要占的高度（随气泡档位缩放）。

    这是**行内文本控件的最小高度**，不能超过卡片的内沿高度
    （row_height − 上下各 bs(1.5) 的卡片内边距），否则布局满足不了最小值就会
    向下溢出：文字被挤到卡片下半部、底部还被裁掉。原来这里额外加了 bs(6)，
    换成微软雅黑后（同字号行高 15→20px）最小值 26 > 行高 23，正是"内容文字
    偏低、出框"的原因。所以这里只取真实行高，余量由 row_height() 负责。
    """
    try:
        from widgets import kit as _kit
        return _kit.text_height(11.25)
    except Exception:
        return 15


def _row_h():
    """单行模块高度：不小于逻辑基准，也不小于基础字体的实际渲染高度。"""
    from widgets import kit
    return kit.row_height()


def _kit_sc_b(v):
    """气泡内尺寸按气泡档位缩放（widgets.kit.bs）。"""
    try:
        from widgets import kit
        return kit.bs(v)
    except Exception:
        return max(1, int(round(v)))


def _kit_sc_qss(qss):
    """气泡内 QSS 按气泡档位缩放（widgets.kit.scale_qss）。"""
    try:
        from widgets import kit
        return kit.scale_qss(qss)
    except Exception:
        return qss


_BTN_MARK = "__btn__"   # 按钮行标记：_disp_rows 的 widget 槽传它，_relayout 生成 _LBtnRow

# 失败原因 → 值区里显示的短标签（按先后顺序匹配，命中即止）
_ERR_HINTS = (
    (("timed out", "timeout", "读取超时", "超时"), "超时"),
    (("getaddrinfo", "name or service not known", "nodename nor servname",
      "temporary failure in name resolution", "无法解析", "dns"), "没网"),
    (("connection refused", "unreachable", "连接被拒", "网络不可达",
      "connectionreset", "connection aborted", "远程主机强迫关闭"), "连不上"),
    (("certificate", "ssl", "证书"), "证书错误"),
    (("401", "unauthorized", "api key", "api_key", "invalid key",
      "incorrect api"), "Key 无效"),
    (("403", "forbidden"), "没权限"),
    (("429", "rate limit", "too many requests"), "太频繁"),
    (("500", "502", "503", "504", "bad gateway", "server error"), "服务端错误"),
    (("404", "not found"), "地址不存在"),
    (("json", "decode", "解析"), "返回格式不对"),
)


def short_error(msg):
    """把一长串报错压成值区能显示的几个字；认不出来就返回空串。

    以前失败只显示"获取失败"、真正的原因藏在标题的悬停提示里——得把鼠标停上去
    才知道是自己断网了还是接口挂了。值区直接给结论，一眼就能判断该不该管。
    """
    low = str(msg or "").lower()
    if not low.strip():
        return ""
    for keys, label in _ERR_HINTS:
        for k in keys:
            if k in low:
                return label
    # HTTP Error 418 之类：至少把状态码捞出来
    import re as _re
    m = _re.search(r"\b([45]\d\d)\b", low)
    return m.group(1) if m else ""


def _forward_ctx_menu(self, event):
    """把行内子控件上的右键转交给气泡本体，弹出模块行右键菜单。

    模块行是真实子控件，右键会先落在行/卡片/文本等子控件上。仅靠 Qt 的事件
    冒泡在部分控件（按钮、带交互的组件）上会被截留，导致"右键禁用/上下移"失效。
    这里显式转交，保证任何子控件上的右键都能命中气泡的 contextMenuEvent。
    """
    try:
        win = self.window()
        handler = getattr(win, "contextMenuEvent", None)
        if handler is not None and win is not self:
            gp = event.globalPos()
            handler(QContextMenuEvent(event.reason(), win.mapFromGlobal(gp), gp))
            event.accept()
            return
    except Exception:
        pass
    event.ignore()


class _HandleLabel(QLabel):
    """左侧拖拽手柄：顶部三点 + 点击手型光标。"""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFixedWidth(_kit_sc_b(9))
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(140, 155, 185, 170))
        # 三点靠近顶部，避免高组件行的手柄显示在中间；点位随气泡档位缩放。
        dot = max(2, _kit_sc_b(4.5))
        half = dot / 2.0
        # 这三个数是喂给 _kit_sc_b 的基准值，和别处一样已含那 1.5
        # （旧版写 4/6/8 再乘 1.5 得 6/9/12）。少乘的话三个点会往上挤 2px、
        # 还会挨得太近连成一条短棍。
        for k in (6.0, 9.0, 12.0):
            y = _kit_sc_b(k)
            p.drawEllipse(QRect(int(self.width() / 2 - half),
                                int(y - half), dot, dot))
        p.end()


class _ScrollText(QWidget):
    """单行横向滚动字幕（文本过长时使用）。"""

    _SPEED = 75.0   # px/s

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._x = 0.0
        self._w = 10
        self._scroll = False
        self._font = _font7()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(_font_h())

    def set_text(self, text, width):
        self._text = str(text)
        # 最小宽度 + 可拉伸：让行内多余空间由值区域吸收，保证手柄统一贴左
        self.setMinimumWidth(max(10, int(width)))
        self.setMaximumWidth(16777215)
        self._x = 0.0
        self.update()

    def _is_scroll(self):
        try:
            fm = QFontMetrics(self._font)
            return fm.horizontalAdvance(self._text) > self.width()
        except Exception:
            return False

    def advance(self, dt):
        if not self._is_scroll():
            return
        fm = QFontMetrics(self._font)
        tw = fm.horizontalAdvance(self._text)
        span = max(1, tw + self.width())
        self._x = (self._x + self._SPEED * dt) % span
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self._font)
        p.setPen(QColor(150, 170, 205))
        if not self._is_scroll():
            # 值与旧版一致：靠右对齐，避免左右不平衡
            p.drawText(self.rect(), Qt.AlignRight | Qt.AlignVCenter, self._text)
            p.end()
            return
        p.setClipRect(self.rect())
        fm = QFontMetrics(self._font)
        tw = fm.horizontalAdvance(self._text)
        w = self.width()
        x = -self._x
        r = QRect(int(x), 0, tw, self.height())
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, self._text)
        p.drawText(QRect(int(x + tw + w), 0, tw, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)
        p.end()


class _ModuleCard(QWidget):
    """模块卡片：浅色底 + 细边框 + 圆角；拖拽时高亮边框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        from widgets import kit
        radius = kit.bubble_token("card_radius")
        edge = max(1, _kit_sc_b(1.5))
        if getattr(self, "_dragging", False):
            p.setPen(QPen(QColor(120, 200, 255, 230), edge))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 42), edge))
        p.setBrush(QColor(255, 255, 255, 11))
        p.drawRoundedRect(self.rect().adjusted(edge, edge, -edge, -edge),
                          radius, radius)
        p.end()


class _LTextRow(QWidget):
    """文字行：手柄（卡片外） + 卡片（标题 + 值）。"""

    _H = 15
    _TITLE_W = 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.handle = _HandleLabel(self)
        lay.addWidget(self.handle)
        # 卡片只包内容，手柄留在左侧槽位（不占卡片内部空间）
        self._card = _ModuleCard(self)
        cl = QHBoxLayout(self._card)
        cl.setContentsMargins(_kit_sc_b(7.5), _kit_sc_b(1.5),
                              _kit_sc_b(7.5), _kit_sc_b(1.5))
        cl.setSpacing(0)
        self.title = QLabel("", self._card)
        from widgets import kit
        self.title.setFixedWidth(kit.bubble_token("title_width"))
        self.title.setFont(_font7())
        # 显式垂直居中：不依赖 QLabel 默认对齐，保证标题与同行的值/按钮基线一致
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title.setStyleSheet("color:#96a7c4;background:transparent;")
        cl.addWidget(self.title)
        self.value = _ScrollText(self._card)
        cl.addWidget(self.value, 1)
        lay.addWidget(self._card, 1)
        self._value_label = None   # 富文本 QLabel（链接可点）时的替代
        self._last_content = None  # 最近一次渲染的内容签名（增量刷新用）
        self._state_kind = "ok"
        self.setFixedHeight(_row_h())

    def set_content(self, title, value, avail_w, pending=False, link_href="",
                    state=None):
        # 签名里必须带上"失败原因的短标签"：同样是 kind=error、值同样是
        # "获取失败"，原因从 503 变成超时时值区要跟着变，不带它就会被增量刷新
        # 判成"没变化"而不重渲染。
        self._last_content = (str(title), str(value or ""),
                              bool(pending), str(link_href),
                              str((state or {}).get("kind", "ok")),
                              short_error((state or {}).get("error")))
        # 标题放不下时省略号 + 悬停提示，避免截断后无法查看完整名称
        try:
            fm_t = QFontMetrics(_font7())
            elided = fm_t.elidedText(str(title), Qt.ElideRight, _kit_sc_b(self._TITLE_W))
        except Exception:
            elided = str(title)
        state = dict(state or {})
        if pending and not str(value or "").strip():
            state.setdefault("kind", "loading")
        kind = str(state.get("kind", "ok"))
        self._state_kind = kind
        from widgets import kit
        color = kit.health_token(kind, "color")
        label = kit.health_token(kind, "label")
        title_text = html.escape(elided)
        # 正常运行不加圆点：健康指示只在加载/过期/错误/暂停等状态提示异常。
        if str(title) and kind != "ok":
            self.title.setText(
                '<span style="color:%s;">●</span> %s' % (color, title_text))
            self.title.setTextFormat(Qt.RichText)
        else:
            self.title.setText(elided)
        tip = [str(title), "状态：%s" % label]
        error = str(state.get("error") or "").strip()
        if error:
            tip.append("详情：%s" % error)
        retry = state.get("retry_in")
        if retry is not None:
            tip.append("下次刷新：%d 秒" % int(retry))
        self.title.setToolTip("\n".join(tip))
        # 清理旧的富文本 QLabel（如果有）
        if self._value_label is not None:
            self._value_label.setParent(None)
            self._value_label.deleteLater()
            self._value_label = None
            self.value.show()
        if pending and not str(value or "").strip():
            self.value.set_text("加载中…", avail_w)
            self.value.setStyleSheet("color:#6f7d96;")
            return
        self.value.setStyleSheet("")
        # 多行内容归一为单行（模块行是单行滚动字幕，换行会被裁成半行）
        plain = str(value or "").replace("\r\n", "\n").replace("\n", " ｜ ")
        # 失败时把原因写进值区：只留"获取失败"的话，得把鼠标停到标题上才知道
        # 是自己断网还是接口挂了。认得出来的才替换，认不出来保持原样。
        if kind == "error":
            hint = short_error(error)
            if hint:
                plain = hint
                self.value.setStyleSheet("color:#e2a0a0;")
        fm = QFontMetrics(_font7())
        if link_href:
            # 智能体待审批：值 + 审批链接
            self._set_rich(value, link_href, avail_w)
            return
        has_link = any(u for _t, u in _split_links(plain))
        if (fm.horizontalAdvance(plain) <= avail_w and "<" not in plain
                and not has_link):
            # 放得下、无特殊字符、无链接：纯文本
            self.value.set_text(plain, avail_w)
            return
        if "<" in plain or has_link:
            # 含 HTML 或链接：用富文本 QLabel（链接可点）
            self._set_rich(plain, "", avail_w)
            return
        self.value.set_text(plain, avail_w)

    def _set_rich(self, value, link_href, avail_w):
        self.value.hide()
        self._value_label = QLabel(self._card)
        self._value_label.setFont(_font7())
        self._value_label.setStyleSheet("color:#96a7c4;background:transparent;")
        self._value_label.setTextFormat(Qt.RichText)
        self._value_label.setOpenExternalLinks(not link_href)
        self._value_label.setWordWrap(False)
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_label.setMinimumWidth(0)
        self._value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        parts = []
        for text, url in _split_links(str(value)):
            text = str(text).replace("\r\n", "\n").replace("\n", " ｜ ")
            if url:
                parts.append('<a href="%s" style="color:#7db6ff;">%s</a>'
                             % (html.escape(url, quote=True), html.escape(text)))
            else:
                parts.append(html.escape(text))
        if link_href:
            parts.append('<a href="%s" style="color:#7db6ff;">［审批］</a>'
                         % html.escape(link_href, quote=True))
        self._value_label.setText("".join(parts))
        self._card.layout().addWidget(self._value_label, 1)
        self._value_label.show()


class _LBtnRow(QWidget):
    """按钮行：与文本行同款标题（左）+ 右侧按钮（值区），单行 15px。
    用于"聚合AI"这类点开悬浮窗的模块，标题样式与其他模块完全一致。"""

    _H = 15
    _TITLE_W = 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.handle = _HandleLabel(self)
        lay.addWidget(self.handle)
        self._card = _ModuleCard(self)
        cl = QHBoxLayout(self._card)
        cl.setContentsMargins(_kit_sc_b(7.5), _kit_sc_b(1.5),
                              _kit_sc_b(7.5), _kit_sc_b(1.5))
        cl.setSpacing(0)
        self.title = QLabel("", self._card)
        from widgets import kit
        self.title.setFixedWidth(kit.bubble_token("title_width"))
        self.title.setFont(_font7())
        # 显式垂直居中：不依赖 QLabel 默认对齐，保证标题与同行的值/按钮基线一致
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title.setStyleSheet("color:#96a7c4;background:transparent;")
        cl.addWidget(self.title)
        self.btn = kit.expand_btn("打开")
        self.btn.setParent(self._card)
        cl.addWidget(self.btn, 1, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self._card, 1)
        self.setFixedHeight(_row_h())
        self._action = None      # 点击回调
        self._poll = None        # 返回按钮文案的轮询函数（可空）
        self.btn.clicked.connect(self._on_click)

    def set_content(self, title, action=None, poll=None):
        self.title.setText(str(title or ""))
        self._action = action
        self._poll = poll
        self._refresh_text()

    def _on_click(self):
        if self._action is not None:
            try:
                self._action()
            except Exception:
                import traceback
                traceback.print_exc()
        self._refresh_text()

    def _refresh_text(self):
        if self._poll is not None:
            try:
                self.btn.setText(str(self._poll()))
                return
            except Exception:
                pass
        self.btn.setText("打开")


class _LWidgetRow(QWidget):
    """交互组件行：手柄（卡片外） + 卡片（标题栏 + 组件本体）。

    标题栏右端有个收起按钮：便签、待办这类"长条"组件很占地方，收起后只剩一条
    标题栏。自带标题栏的组件（画布/拼豆/统计/Token/对话面板）本来就有自己的
    展开控件，这里不会重复加——它们传进来的标题是空的，整条标题栏都不显示。
    """

    _TITLE_H = 15          # 标题栏高度（有标题时）
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.handle = _HandleLabel(self)
        self._lay.addWidget(self.handle)
        self._card = _ModuleCard(self)
        self._card_lay = QVBoxLayout(self._card)
        # 与文本行卡片一致，紧凑单行；间距也随气泡档位缩放。
        self._card_lay.setContentsMargins(_kit_sc_b(7.5), _kit_sc_b(1.5),
                                          _kit_sc_b(7.5), _kit_sc_b(1.5))
        self._card_lay.setSpacing(_kit_sc_b(3))
        # 标题栏：标题 + 右端展开/收起按钮（在卡片内顶部）
        from widgets import kit
        self._head = QWidget(self._card)
        self._head.setAttribute(Qt.WA_TranslucentBackground, True)
        _hb = QHBoxLayout(self._head)
        _hb.setContentsMargins(0, 0, 0, 0)
        _hb.setSpacing(_kit_sc_b(6))
        self._title_label = QLabel("", self._head)
        self._title_label.setFont(_font7())
        self._title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title_label.setStyleSheet("color:#96a7c4;background:transparent;")
        _hb.addWidget(self._title_label, 1)
        # 用组件自带的那颗带底色的「展开」按钮（kit.expand_btn），不另造一套：
        # 画布/拼豆/统计/Token 的折叠键都是它，样式与文案必须一致。
        self.fold_btn = kit.expand_btn("收起")
        self.fold_btn.setToolTip("收起 / 展开这个模块")
        self.fold_btn.clicked.connect(self._on_fold_clicked)
        _hb.addWidget(self.fold_btn, 0)
        # 标题栏高度必须正好是 row_height：title_extra() 按它算行高预算
        self._head.setFixedHeight(kit.row_height())
        self._head.hide()
        self._card_lay.addWidget(self._head)
        self._lay.addWidget(self._card, 1)
        self._widget = None
        self._collapsed = False
        self.on_fold = None        # 由气泡挂上：点了收起按钮之后谁来播动画/存状态

    def _on_fold_clicked(self):
        if self.on_fold is not None:
            self.on_fold(self)

    def set_collapsed(self, on):
        """收起 = 藏掉组件本体，只留标题栏（高度由外层按 title_extra 给）。"""
        on = bool(on)
        self._collapsed = on
        self.fold_btn.setText("展开" if on else "收起")
        if self._widget is not None:
            self._widget.setVisible(not on)

    def is_collapsed(self):
        return bool(self._collapsed)

    def set_title(self, title):
        t = str(title or "").strip()
        self._title_label.setText(t)
        # 整条标题栏（标题 + 收起按钮）跟着标题一起显隐：自带标题栏的组件
        # 传空标题进来，它们有自己的展开控件，不该再多一条
        self._head.setVisible(bool(t))
        # 无框架标题的组件（画布/拼豆/统计/Token 等自带标题栏）：卡片上下内边距
        # 归零，把整行高度让给组件。否则组件按 row_height 算好的 15px 会被上下
        # 各 1px 边距挤成 13px，自带标题栏里的「展开」按钮就嵌不正。
        m_v = _kit_sc_b(1.5) if t else 0
        self._card_lay.setContentsMargins(_kit_sc_b(7.5), m_v, _kit_sc_b(7.5), m_v)

    def has_title(self):
        # 用 isHidden 而非 isVisible：布局阶段组件可能尚未显示，isVisible 会误判
        return not self._head.isHidden()

    def title_extra(self):
        """Exact vertical chrome added above an embedded widget."""
        if not self.has_title():
            return 0
        from widgets import kit
        m = self._card_lay.contentsMargins()
        return (kit.row_height() + self._card_lay.spacing()
                + m.top() + m.bottom())

    def collapsed_height(self):
        """收起之后这一行有多高：只剩标题栏那一条。"""
        from widgets import kit
        m = self._card_lay.contentsMargins()
        return kit.row_height() + m.top() + m.bottom()

    def set_state(self, state):
        state = dict(state or {})
        from widgets import kit
        self._title_label.setToolTip(
            "%s\n状态：%s" % (self._title_label.text(),
                             kit.health_token(state.get("kind", "ok"), "label")))

    def set_widget(self, w):
        if self._widget is w:
            return
        if self._widget is not None and self._widget.parent() is self._card:
            self._card_lay.removeWidget(self._widget)
            self._widget.hide()
        self._widget = w
        self._card_lay.addWidget(w, 1)
        w.setMinimumWidth(0)   # 允许组件缩到可用宽度，避免把行撑出气泡
        w.setSizePolicy(QSizePolicy.Ignored, w.sizePolicy().verticalPolicy())
        w.show()


class StatusBubbleLayout(StatusBubble):
    """实验版气泡：内容用 QVBoxLayout 排布，替代手写坐标（对比版本）。"""

    _LAYOUT = True
    _OUT_M = 2       # 气泡左右外边距（已减半）
    _HANDLE_W = 6    # 手柄宽度（在边距内居中）

    def __init__(self, pet_widget):
        super().__init__(pet_widget)
        self._content = QWidget(self)
        self._content.setAttribute(Qt.WA_TranslucentBackground, True)
        # 行按列摆：一列装不下（超出屏幕可用高度）就往旁边再开一列。气泡整体
        # 以桌宠为中心摆放，所以多出来的列自然是向两边长，不会顶出屏幕。
        # 列的位置**自己 setGeometry**，不套横向布局：气泡是隐藏状态下算好尺寸
        # 再显示的，嵌套布局在隐藏窗口上不会及时跑，行会全挤在一起。
        self._columns = []           # 列容器控件（第 0 列的布局就是 self._vbox）
        from widgets import kit
        self._out_m = kit.bubble_token("outer_margin")
        self._handle_w = kit.bubble_token("handle_width")
        self._vbox = self._new_column()
        self._row_widgets = []       # 当前布局中的行控件（按显示顺序，跨列连续）
        self._wrap_cache = {}        # 规则 id -> 交互组件包装行（复用）
        self._scroll_rows = []       # 滚动字幕行
        self._drag2 = None           # 布局版拖拽状态
        self._last_keys = None       # 最近一次重排的行键（避免无变化时反复重建导致闪动）
        self._instant_resize = False # 对话面板折叠动画期间：即时改尺寸，不做整体补位动画
        self._fold_locked = False    # 对话折叠动画期间：禁止刷新重排/定位，避免整面板闪烁跳动
        self._insert_line = None     # 拖拽插入高亮线
        self._drag_pet = None        # 拖动气泡顶栏移动桌宠的状态
        # 气泡透明度滑块（钉住按钮左侧）
        self._bubble_opacity = float(
            getattr(self.pet, "settings", {}).get("bubble_opacity", 0.95) or 0.95)
        self._op_slider = QSlider(Qt.Horizontal, self)
        self._op_slider.setRange(30, 100)
        self._op_slider.setValue(int(round(self._bubble_opacity * 100)))
        self._op_slider.setFixedSize(_kit_sc_b(63), _kit_sc_b(21))
        self._op_slider.setCursor(Qt.PointingHandCursor)
        self._op_slider.setToolTip("气泡透明度")
        self._op_slider.setStyleSheet(_kit_sc_qss(
            "QSlider::groove:horizontal{height:4.5px;"
            "background:rgba(255,255,255,45);border-radius:1.5px;}"
            "QSlider::handle:horizontal{width:10.5px;height:10.5px;margin:-4.5px 0;"
            "border-radius:6px;background:rgba(200,215,240,210);}"))
        self._op_slider.valueChanged.connect(self._on_bubble_opacity)
        # 内容层会盖住右上角按钮，必须把钉住/关闭按钮提到最上层
        self._pin_btn.raise_()
        self._fold_btn.raise_()
        self._op_slider.raise_()
        self._content.show()
        self._maybe_ui_timer()

    # ---------- 分列 ----------
    def _new_column(self):
        """新开一列，返回它的竖向布局。

        每列的内外边距和原来单列时一模一样（右边距 = 左边距 + 手柄槽，让卡片
        左右留白相等），所以列与列之间自带一条均匀的空隙，不用额外 spacing。
        """
        col = QWidget(self._content)
        col.setAttribute(Qt.WA_TranslucentBackground, True)
        # 每列固定单列宽：横向布局按 sizeHint 分配空间，不定宽的话行会被压扁
        col.setFixedWidth(int(getattr(self, "_FIX_W", 200)))
        v = QVBoxLayout(col)
        v.setContentsMargins(self._out_m,
                             max(0, self._HEAD_H - _kit_sc_b(4.5)),
                             self._out_m + self._handle_w,
                             _kit_sc_b(3))
        v.setSpacing(_kit_sc_b(1.5))
        col.show()
        self._columns.append(col)
        return v

    def _col_layout(self, i):
        while len(self._columns) <= i:
            self._new_column()
        return self._columns[i].layout()

    def _max_col_h(self):
        """一列最多多高：桌宠所在屏幕的可用高度留一点余量。

        超过就新开一列——宁可横着长，也不要竖着长到屏幕外面去（长出去的部分
        既看不见也点不到）。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            scr = QApplication.screenAt(
                self.pet.mapToGlobal(self.pet.rect().center())) \
                or QApplication.primaryScreen()
            return max(_kit_sc_b(240), scr.availableGeometry().height() - _kit_sc_b(42))
        except Exception:
            return 10 ** 6          # 取不到屏幕信息就别分列

    def _row_h(self, w):
        """行的权威高度：sizeHint 在复用行上会给过期缓存，用 minimumHeight。"""
        return max(10, int(w.minimumHeight()))

    def _split_columns(self, wraps):
        """把行按顺序切成若干列，返回 [[行, ...], ...]。

        两步：
        1) 先按"一列最多多高"（屏幕可用高度）贪心装一遍，确定**需要几列**；
        2) 列数定下来之后，二分找"仍然只要这么多列"的最小列高，把高度均摊开。

        只做第 1 步的话，第一列会一直顶到屏幕底，第二列只剩零星几个模块，
        短列下面拖着一大片空白（用户反馈"这么大空间很浪费"）。均摊之后两列
        高度接近，整个气泡矮一大截。

        模块的相对顺序始终不变（用户拖拽排的序不能被打乱）；单行本身就超高
        （比如展开的大组件）时它独占一列。
        """
        limit = self._max_col_h()
        m = self._vbox.contentsMargins()
        pad = m.top() + m.bottom() + _kit_sc_b(6)
        gap = self._vbox.spacing()

        def pack(h_limit):
            cols, cur, cur_h = [], [], pad
            for w in wraps:
                h = self._row_h(w)
                if cur and cur_h + gap + h > h_limit:
                    cols.append(cur)
                    cur, cur_h = [], pad
                cur_h += h + (gap if cur else 0)
                cur.append(w)
            if cur or not cols:
                cols.append(cur)
            return cols

        cols = pack(limit)
        n_cols = len(cols)
        if n_cols <= 1 or not wraps:
            return cols
        # 下界是"最高的那一行"——比它还矮的列高装不下那一行，二分会白跑
        lo = max(self._row_h(w) for w in wraps) + pad
        hi = limit
        best = cols
        while lo <= hi:
            mid = (lo + hi) // 2
            c = pack(mid)
            if len(c) <= n_cols:
                best, hi = c, mid - 1
            else:
                lo = mid + 1
        return best

    def _groups_sig(self, groups):
        return [[id(w) for w in g] for g in groups]

    def _layout_rows(self, wraps):
        """按需分列后把行摆进各列；多余的空列收起来。"""
        for col in self._columns:
            lay = col.layout()
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        groups = self._split_columns(wraps)
        for i, group in enumerate(groups):
            lay = self._col_layout(i)
            for wrap in group:
                wrap.setParent(self._columns[i])
                lay.addWidget(wrap)
                wrap.show()   # setParent 会隐式隐藏，必须重新显示
            # 末尾加弹簧：短的那一列剩下的空间要全丢到底部，否则 Qt 会把它
            # 平摊到各行之间，同一个气泡里两列的行距对不齐
            lay.addStretch(1)
            self._columns[i].show()
        for i in range(len(groups), len(self._columns)):
            self._columns[i].hide()
        self._col_groups = self._groups_sig(groups)
        return len(groups)

    def _row_rect(self, w):
        """行在 _content 坐标系里的矩形。

        分列之后行的父控件是"列"，`geometry()` 是列内坐标，拖拽命中和插入线
        都得换算到 _content 上来，否则第二列的行永远点不中。
        """
        return QRect(w.mapTo(self._content, QPoint(0, 0)), w.size())

    def _on_bubble_opacity(self, v):
        try:
            self._bubble_opacity = max(0.3, min(1.0, v / 100.0))
            if not self._anim_dir:
                self.setWindowOpacity(self._bubble_opacity)
            s = getattr(self.pet, "settings", None)
            if s is not None:
                s["bubble_opacity"] = self._bubble_opacity
                try:
                    import pet_gravity
                    pet_gravity.save_settings(s)
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- 呈现层覆盖 ----------

    def _row_state(self, index, title, pending=False):
        """Return the health state for a row; old callers without states stay ok."""
        states = getattr(self, "_row_states", [])
        state = dict(states[index]) if 0 <= index < len(states) else {}
        state.setdefault("kind", "loading" if pending else "ok")
        if pending and not state.get("error"):
            state["kind"] = "loading"
        state.setdefault("title", title)
        return state

    def _relayout(self):
        if self._fold_locked:
            return   # 折叠动画期间由对话面板自己控制，不做整面板重排
        try:
            rows = self._disp_rows if self._disp_rows else self._raw_rows
            width = self._FIX_W
            # 值区宽度 = 卡片宽 - 卡片内边距 - 标题
            avail = (width - 2 * (self._out_m + self._handle_w)
                     - _kit_sc_b(15) - self._title_w - _kit_sc_b(3))
            rows_data = []
            keys = []
            for i, (title, value, widget) in enumerate(rows):
                rule_id = self._row_rule_ids[i] if i < len(self._row_rule_ids) else None
                if widget == _BTN_MARK:
                    key = rule_id or ("b:%d" % i)
                    keys.append(("b", key))
                    rows_data.append(("b", key, title, value, None))
                elif widget is not None and _w_is_alive(widget):
                    key = rule_id or ("w:%d" % i)
                    keys.append(("w", key))
                    rows_data.append(("w", key, title, value, widget))
                else:
                    key = rule_id or title
                    keys.append(("t", key))
                    rows_data.append(("t", key, title, value, None))
            if keys == self._last_keys and self._row_widgets:
                # 行集合未变化：原地更新内容/高度，避免反复重建导致展开/收起闪动
                self._update_rows_inplace(rows_data, avail)
                return
            new_wraps = []
            used_cache = set()
            self._scroll_rows = []
            for kind, key, title, value, widget in rows_data:
                if kind == "b":
                    wrap = self._wrap_cache.get(key)
                    if wrap is None or not isinstance(wrap, _LBtnRow):
                        wrap = _LBtnRow(self._content)
                        self._wrap_cache[key] = wrap
                    wrap.set_content(title,
                                     action=self._on_btn_click,
                                     poll=self._btn_text)
                    wrap.setFixedWidth(width - self._out_m
                                       - (self._out_m + self._handle_w))
                    wrap._ridx = len(new_wraps)
                    new_wraps.append(wrap)
                    used_cache.add(key)
                    continue
                if kind == "w":
                    wrap = self._wrap_cache.get(key)
                    if wrap is None or not _w_is_alive(wrap):
                        wrap = _LWidgetRow(self._content)
                        self._wrap_cache[key] = wrap
                    wrap.set_widget(widget)
                    # 标题栏：自带标题栏/折叠按钮的组件（对话面板、画布/拼豆/统计/
                    # Token、**待办**）整条框架标题栏不加 —— 否则两颗"收起/展开"
                    # 叠在一起（用户实测待办：框架一颗 + 组件自己的 _fold_btn 一颗）。
                    # 名单收口在 module_core._TITLE_LESS_WIDGET_UIS，别在这里散着认类型
                    # （以前只认 ChatPanel，待办就是这么漏的）。
                    import module_core as _mc
                    _ui = str(getattr(widget, "oi_ui", "") or "")
                    _own_head = (isinstance(widget, ChatPanel)
                                 or _ui in _mc._TITLE_LESS_WIDGET_UIS)
                    wrap.set_title("" if _own_head else title)
                    wrap.on_fold = self._on_row_fold
                    wrap.set_collapsed(self._is_row_collapsed(key))
                    wrap.setFixedWidth(width - self._out_m
                                       - (self._out_m + self._handle_w))
                    wrap.setFixedHeight(self._wrap_height(wrap, widget, width, key))
                    wrap._ridx = len(new_wraps)
                    new_wraps.append(wrap)
                    used_cache.add(key)
                    continue
                row = _LTextRow(self._content)
                pending = bool(title in self._pending_titles)
                link_href = ""
                p = self._agent_map.get(title)
                if p is not None and p.pending_request() and str(value or "").startswith("待审批"):
                    link_href = "approve://" + str(title)
                row.set_content(title, value, avail, pending=pending,
                                link_href=link_href,
                                state=self._row_state(i, title, pending))
                if row._value_label is not None:
                    try:
                        row._value_label.linkActivated.connect(self._on_row_link)
                    except Exception:
                        pass
                elif not pending:
                    self._scroll_rows.append(row.value)
                row._ridx = len(new_wraps)
                new_wraps.append(row)
            # 按新顺序重排布局（装不下就分列）
            self._layout_rows(new_wraps)
            # 删除不再使用的包装行
            for key, wrap in list(self._wrap_cache.items()):
                if key not in used_cache:
                    try:
                        wrap.setParent(None)
                        wrap.deleteLater()
                    except Exception:
                        pass
                    self._wrap_cache.pop(key, None)
            self._row_widgets = new_wraps
            self._last_keys = keys
            self._apply_size(width)
        except Exception:
            import traceback
            traceback.print_exc()

    def _update_rows_inplace(self, rows_data, avail):
        """行集合未变化：原地更新内容与高度（不重建控件，避免闪动）。"""
        try:
            self._scroll_rows = []
            for i, (kind, key, title, value, widget) in enumerate(rows_data):
                wrap = self._row_widgets[i]
                wrap._ridx = i
                if kind == "b":
                    if not isinstance(wrap, _LBtnRow):
                        wrap = _LBtnRow(self._content)
                        self._row_widgets[i] = wrap
                    wrap.set_content(title,
                                     action=self._on_btn_click,
                                     poll=self._btn_text)
                    wrap.show()
                    continue
                if kind == "w":
                    if widget is not None and _w_is_alive(widget):
                        wrap.set_widget(widget)
                        wrap.set_title(title
                                       if not isinstance(widget, ChatPanel) else "")
                        wrap.on_fold = self._on_row_fold
                        wrap.setFixedWidth(self._FIX_W - self._out_m
                                           - (self._out_m + self._handle_w))
                        target = self._wrap_height(wrap, widget, self._FIX_W, key)
                        # 收起动画进行中的那一行别动它的高度，否则每次刷新都会
                        # 把动画中间值打回终值，看起来就是一顿一顿的
                        busy = (getattr(self, "_rowfold", None) or {}).get("wrap")
                        if busy is not wrap and int(wrap.minimumHeight()) != target:
                            wrap.setFixedHeight(target)
                        if wrap.has_title():
                            wrap.set_state(self._row_state(i, title))
                        wrap.show()          # 隐藏->重开时确保重新显示
                        if not wrap.is_collapsed():
                            widget.show()
                    continue
                pending = bool(title in self._pending_titles)
                link_href = ""
                p = self._agent_map.get(title)
                if p is not None and p.pending_request() and str(value or "").startswith("待审批"):
                    link_href = "approve://" + str(title)
                state = self._row_state(i, title, pending)
                sig = (str(title), str(value or ""), bool(pending), str(link_href),
                       str(state.get("kind", "ok")),
                       short_error(state.get("error")))
                if getattr(wrap, "_last_content", None) != sig:
                    # 内容变化才重渲染，未变化的行零开销（增量刷新）
                    wrap.set_content(title, value, avail,
                                     pending=pending, link_href=link_href,
                                     state=state)
                    if wrap._value_label is not None:
                        try:
                            wrap._value_label.linkActivated.connect(self._on_row_link)
                        except Exception:
                            pass
                    elif not pending:
                        self._scroll_rows.append(wrap.value)
                elif wrap._value_label is None and not pending:
                    self._scroll_rows.append(wrap.value)
            for wrap in self._row_widgets:
                wrap.show()
            self._apply_size(self._FIX_W)
        except Exception:
            import traceback
            traceback.print_exc()

    # ---------- 组件行的收起 / 展开 ----------
    def _collapsed_keys(self):
        """哪些模块是收起的：存进 settings，重启之后还记得。"""
        s = getattr(self.pet, "settings", None)
        if not isinstance(s, dict):
            return set()
        v = s.get("collapsed_widgets")
        if not isinstance(v, list):
            v = []
            s["collapsed_widgets"] = v
        return set(str(x) for x in v)

    def _is_row_collapsed(self, key):
        return str(key) in self._collapsed_keys()

    def _wrap_height(self, wrap, widget, width, key):
        """组件行该多高：收起就只留标题栏，展开按组件自己要的高度。"""
        if wrap.is_collapsed():
            return wrap.collapsed_height()
        # 显式指定交互行高度：对话面板等没有 sizeHint，交给布局会缩成一行
        wh = (widget.current_height() if hasattr(widget, "current_height")
              else _widget_height(widget, width - 24))
        return max(10, int(wh)) + wrap.title_extra()

    def _wrap_of(self, widget):
        """找到装着这个组件的那一行。"""
        for w in self._row_widgets:
            if _w_is_alive(w) and getattr(w, "_widget", None) is widget:
                return w
        return None

    def _key_of(self, wrap):
        for k, w in self._wrap_cache.items():
            if w is wrap:
                return str(k)
        return None

    def _on_widget_resize(self, widget):
        """组件自己改了高度（画布/拼豆/统计/Token 的「展开」按钮）：平滑过渡。

        以前这里直接 `_relayout`——瞬间重建。展开时因为紧跟着还有一次整框补位
        动画，看着像"有动画"；收起时补位动画方向相反、又被新尺寸立刻盖掉，就成了
        用户说的"一帧跳回去"。现在和行上的收起按钮共用同一条动画路径，两个方向
        完全对称。
        """
        try:
            wrap = self._wrap_of(widget)
            if wrap is None or not self.isVisible() or self._fold_locked:
                self._relayout()
                return
            h0 = wrap.height()
            h1 = self._wrap_height(wrap, widget, self._FIX_W,
                                   self._key_of(wrap))
            if abs(int(h1) - int(h0)) <= 1:
                self._relayout()      # 高度没变（比如只是刷新了内容）
                return
            self._start_row_fold(wrap, h0, h1)
        except Exception:
            self._relayout()

    def _on_row_fold(self, wrap):
        """点了组件行标题栏上的收起按钮：存状态 + 播一段高度动画。"""
        try:
            key = self._key_of(wrap)
            target = not wrap.is_collapsed()
            s = getattr(self.pet, "settings", None)
            if isinstance(s, dict) and key:
                keys = self._collapsed_keys()
                keys.add(key) if target else keys.discard(key)
                s["collapsed_widgets"] = sorted(keys)
                try:
                    import pet_gravity
                    pet_gravity.save_settings(s)
                except Exception:
                    pass
            h0 = wrap.height()
            wrap.set_collapsed(target)
            h1 = self._wrap_height(wrap, wrap._widget, self._FIX_W, key)
            self._start_row_fold(wrap, h0, h1)
        except Exception:
            import traceback
            traceback.print_exc()

    def _start_row_fold(self, wrap, h0, h1):
        """组件行高度动画：和对话面板折叠用同一套口径（60fps + 两头慢的缓动）。

        动画期间置 `_fold_locked`：气泡的定时刷新会走 `_relayout` 重建所有行，
        正在动的那一行会被连根删掉（实测 RuntimeError: object has been deleted）。
        对话面板的折叠本来就是这么挡的，这里沿用同一把锁。

        锚点在动画开始时一次性取好（当前所在侧的外边缘），每帧直接摆几何，
        整框不再走 `_start_settle` 的补位动画——否则行和框两段动画各跑各的，
        框慢半拍、末尾补几像素，就是用户说的"最后弹一下"。
        """
        from bubble_ui import ANIM_MS, _dist_dur
        self._fold_locked = True
        side = self._current_side()
        if side == "below":
            anchor, mode = self.y(), "top"
        else:
            anchor, mode = self.y() + self.height(), "bottom"
        self._rowfold = {"wrap": wrap, "h0": float(h0), "h1": float(h1),
                         "t0": time.monotonic(), "dur": _dist_dur(h1 - h0),
                         "anchor": anchor, "mode": mode, "fx": self.x()}
        # 终局的 x：先按"行已经是目标高度"算一遍尺寸，再问摆位逻辑该放哪儿。
        # 收起一个大组件可能让两列缩成一列，宽度一变、侧边摆放的居中位置就变。
        try:
            self._rowfold["x0"] = self.x()
            _h_keep = wrap.height()
            wrap.setFixedHeight(max(10, int(round(h1))))
            _tw, _fh, _chs, _cols = self._measure()
            wrap.setFixedHeight(_h_keep)
            # _compute_target_geom 读的是 self.width()（当前宽度），而我们要问的是
            # "宽度变成 _tw 之后该摆哪儿"。所以先把窗口宽度临时设成终局值再问，
            # 问完立刻复原——不这么做算出来的还是旧宽度对应的位置。
            _w_keep = (self.minimumWidth(), self.maximumWidth(), self.width())
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.resize(int(_tw), self.height())
            _tx, _ty, _mode = self._compute_target_geom(
                _fh, self._side_hint or self._current_side())
            self.resize(_w_keep[2], self.height())
            self.setMinimumWidth(_w_keep[0])
            self.setMaximumWidth(_w_keep[1])
            self._rowfold["x1"] = int(_tx)
        except Exception:
            self._rowfold["x0"] = self._rowfold["x1"] = self.x()
        try:
            self._mv_timer.stop()      # 位置缓动会和折叠几何互相拉扯
        except Exception:
            pass
        if getattr(self, "_rowfold_timer", None) is None:
            self._rowfold_timer = QTimer(self)
            self._rowfold_timer.setTimerType(Qt.PreciseTimer)
            self._rowfold_timer.timeout.connect(self._rowfold_tick)
        self._rowfold_timer.start(ANIM_MS)
        self._rowfold_tick()

    def _rowfold_frame(self, a, h):
        """把这一帧的行高摆进去，并同步整框几何（与对话折叠同一条路径）。"""
        a["wrap"].setFixedHeight(max(10, int(round(h))))
        total_w, full_h, col_hs, cols = self._measure()
        # 分列结果变了（收起一个大组件可能让两列缩成一列）就当场重排一次：
        # 行是物理放在各列的布局里的，只改窗口宽度的话，第二列那些行会被裁在
        # 窗口外面直接看不见。只在签名变化时做，不是每帧都做。
        if self._groups_sig(cols) != getattr(self, "_col_groups", None):
            rows = [w for w in self._row_widgets if _w_is_alive(w)]
            self._layout_rows(rows)
            total_w, full_h, col_hs, cols = self._measure()
        self._full_h = full_h
        self._col_panels = [(i * self._FIX_W, 0, self._FIX_W, ch)
                            for i, ch in enumerate(col_hs)]
        self._fold_apply_geom(a["anchor"], a["mode"], a["fx"],
                              total_w, full_h, col_hs)

    def _rowfold_tick(self):
        from bubble_ui import ease_in_out
        a = getattr(self, "_rowfold", None)
        if not a or not _w_is_alive(a["wrap"]):
            if getattr(self, "_rowfold_timer", None) is not None:
                self._rowfold_timer.stop()
            self._rowfold = None
            self._fold_locked = False
            return
        k = min(1.0, (time.monotonic() - a["t0"]) / max(0.01, a["dur"]))
        # 横向锚点跟着走：宽度在动画里可能变（两列缩成一列），而气泡在桌宠侧边
        # 时是"居中于桌宠"的，宽度一变目标 x 就变。不在动画里插值的话，动画结束
        # 后位置缓动才开始追，看着就是收完了又滑一段。
        a["fx"] = self._fold_x_at(a, ease_in_out(k))
        self._rowfold_frame(a, a["h0"] + (a["h1"] - a["h0"]) * ease_in_out(k))
        if k >= 1.0:
            self._rowfold_timer.stop()
            self._rowfold = None
            a["fx"] = self._fold_x_at(a, 1.0)
            self._rowfold_frame(a, a["h1"])   # 末帧精确落在目标上，不留零头
            self._fold_locked = False
            self._place()

    def _fold_x_at(self, a, e):
        """折叠动画进行到缓动值 e 时，窗口该在哪个 x。

        起点是点击那一刻的 x，终点是"按终局宽度算出来的 x"——两端之间线性插值，
        于是宽度、高度、位置在同一段动画里一起到位。
        """
        x0 = a.get("x0")
        if x0 is None:
            return a["fx"]
        x1 = a.get("x1")
        if x1 is None:
            return x0
        return int(round(x0 + (x1 - x0) * e))

    def _measure(self, col_w=None):
        """算出当前该多大：返回 (总宽, 总高, 每列高度, 每列的行)。

        `_apply_size`（正常布局）和 `_fold_set_height`（折叠动画每帧）必须用
        **同一套算法**——两边各算各的，动画结束时算出来的尺寸差那么几像素，
        收尾时就会再补一次几何动画，看起来就是"最后弹一下"。
        """
        col_w = int(col_w or self._FIX_W)
        m = self._vbox.contentsMargins()
        gap = self._vbox.spacing()
        pad = m.top() + m.bottom() + _kit_sc_b(6)
        floor_h = self._HEAD_H + _kit_sc_b(12)
        rows = [w for w in self._row_widgets if _w_is_alive(w)]
        cols = self._split_columns(rows)
        col_hs = []
        for group in cols:
            t = sum(self._row_h(w) for w in group) + max(0, len(group) - 1) * gap
            col_hs.append(max(floor_h, t + pad))
        if not col_hs:
            col_hs = [floor_h]
            cols = [[]]
        return col_w * len(col_hs), max(col_hs), col_hs, cols

    def _apply_size(self, width):
        """按行高累加计算气泡尺寸并应用（含展开/收起动画与补位）。

        分列之后：高度取**最高的那一列**，宽度是列数 × 单列宽。
        """
        # 用 minimumHeight（=setFixedHeight 的权威值），sizeHint 在复用行时会返回过期缓存
        rows = [w for w in self._row_widgets if _w_is_alive(w)]
        cols = self._split_columns(rows)
        # 行高变了（比如展开了一个交互组件）可能导致分列结果变化——这里走的是
        # 原地更新路径，没有重排过，发现不一致就补一次重排，否则算出来的尺寸
        # 和实际摆放对不上。
        if self._groups_sig(cols) != getattr(self, "_col_groups", None):
            self._layout_rows(rows)
        col_w = int(width)
        for col in self._columns:
            col.setFixedWidth(col_w)           # 档位变了列宽也要跟着变
        # 尺寸一律走 _measure：折叠动画每帧用的是同一套算法，两边算出来必须
        # 一模一样，否则动画收尾会再补一段几何动画（"最后弹一下"）
        width, full_h, col_hs, cols = self._measure(col_w)
        ncols = max(1, len(col_hs))
        self._ncols = ncols
        self._full_h = full_h
        # 每列一块背景板（画在 paintEvent 里）：紧贴排列，各自高度自适应
        self._col_panels = [(i * col_w, 0, col_w, h) for i, h in enumerate(col_hs)]
        self._apply_col_mask(width, full_h)
        self.setFixedWidth(width)
        self._content.setGeometry(0, 0, width, full_h)
        # 列自己摆位 + 立刻跑一次列内布局：气泡是隐藏着算好尺寸再显示的，
        # 靠 Qt 自己调度的话这两步要等到显示之后才发生，其间行的坐标全是错的
        # （拖拽命中会点错行，同一列的行还会挤在同一个 y 上）。
        for i, col in enumerate(self._columns):
            if i < ncols:
                col.setGeometry(i * col_w, 0, col_w, col_hs[i])
                col.show()
            else:
                col.hide()
            lay = col.layout()
            if lay is not None:
                lay.activate()
        # 右上角悬浮控件定位：钉住 / 关闭 / 透明度滑块，尺寸与间距随气泡档位一起缩放，
        # 否则按钮放大后仍按固定偏移会重叠错位。
        _btn_w = _kit_sc_b(24)
        _slider_w = _kit_sc_b(63)
        _rm = _kit_sc_b(9)      # 关闭按钮距右边缘
        _gap1 = _kit_sc_b(3)    # 钉住↔关闭 间距
        _gap2 = _kit_sc_b(6)    # 透明度↔钉住 间距
        _fold_x = width - _rm - _btn_w
        _pin_x = _fold_x - _gap1 - _btn_w
        _slider_x = _pin_x - _gap2 - _slider_w
        # 三个控件在标题栏内统一垂直居中。视觉标题带是气泡圆角框内的 [1, _HEAD_H]，
        # 取其整数中心后各自减去半高——必须用整数运算：先前用 round() 时
        # 银行家舍入让 2.5→2 而 3.5→4，导致滑块比按钮低 1px（小档位下很明显）。
        _head = max(_kit_sc_b(21), int(getattr(self, "_HEAD_H", 0) or _kit_sc_b(30)))
        _band_c = (1 + _head) // 2
        _btn_y = max(0, _band_c - self._pin_btn.height() // 2)
        _sld_y = max(0, _band_c - self._op_slider.height() // 2)
        self._pin_btn.move(_pin_x, _btn_y)
        self._fold_btn.move(_fold_x, _btn_y)
        self._op_slider.move(_slider_x, _sld_y)
        if self.isVisible() and not self._anim_dir:
            if self.width() != width or self.height() != full_h:
                if self._instant_resize:
                    # 只让当前模块（对话面板）折叠：整框即时跟尺寸，底边保持不动
                    self._instant_resize = False
                    self.setGeometry(self.x(),
                                     self.y() + (self.height() - full_h),
                                     width, full_h)
                else:
                    self._start_settle(width, full_h)
        elif self._anim_dir:
            k = min(1.0, (time.monotonic() - self._anim_t0) / max(0.01, self._anim_dur))
            e = 1.0 - (1.0 - k) ** 3
            self._apply_height(int(self._anim_h0 + (self._anim_h1 - self._anim_h0) * e))
        else:
            self.resize(width, full_h)
        self._layout_card_host()
        self._maybe_ui_timer()

    def _fold_set_height(self, panel, wh):
        """对话折叠动画每帧：模块外框高度与气泡窗口高度同步（锚点固定），
        窗口随模块“顶长/收回”，其他行只做位置跟随、高度不变，不整框拉伸。"""
        try:
            for wrap in self._row_widgets:
                if getattr(wrap, "_widget", None) is panel:
                    wrap.setFixedHeight(max(10, int(wh)))
                    break
            # 用和正常布局完全同一套算法算尺寸（见 _measure 的说明），
            # 否则动画结束时差几像素，收尾会再补一段几何动画 = "最后弹一下"
            total_w, full_h, col_hs, _cols = self._measure()
            self._full_h = full_h
            self._col_panels = [(i * self._FIX_W, 0, self._FIX_W, h)
                                for i, h in enumerate(col_hs)]
            anchor = getattr(panel, "_fold_anchor", None)
            if anchor is None:
                anchor = self.y() + self.height()
            mode = getattr(panel, "_fold_mode", "bottom")
            fx = getattr(panel, "_fold_x", None)
            if fx is None:
                fx = self.x()
            self._fold_apply_geom(anchor, mode, fx, total_w, full_h, col_hs)
        except Exception:
            pass

    def _fold_apply_geom(self, anchor, mode, fx, total_w, full_h, col_hs):
        """折叠动画每帧的几何：窗口锚在一条不动的边上，只让另一边长短。

        必须直接 setGeometry，**不能**走 `_apply_size` 的正常路径——那条路会把
        整框尺寸交给 `_start_settle` 再做一段 0.16s 补位动画，于是行在动、框也在
        动，两段动画各算各的时间，框永远慢半拍、结束时再补最后几像素，看着就是
        "收完还弹一下"。对话面板的折叠一直是走这里，组件行折叠现在也走同一条。
        """
        if mode == "top":
            y = anchor
        else:
            y = anchor - full_h
        # 宽度必须先解锁：正常布局走的是 `setFixedWidth()`（把 min=max 锁死），
        # 折叠动画里 setGeometry 根本改不动宽度。列数在动画中变少时（比如收起一个
        # 大组件，两列缩成一列），窗口会一直保持旧宽度，等动画结束、下一次定时
        # 刷新再 setFixedWidth 才一帧切过去 —— 实测宽度从 630 一帧跳到 315，
        # 就是用户说的"最后一刻跳一下"。
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        # 先定窗口几何（锚点固定），再跟内容，避免内容先移动露出窗口外造成残影
        self.setGeometry(fx, y, total_w, full_h)
        for col in self._columns:
            col.setFixedWidth(int(self._FIX_W))
        self._content.setGeometry(0, 0, total_w, full_h)
        for i, col in enumerate(self._columns):
            if i < len(col_hs):
                col.setGeometry(i * self._FIX_W, 0, self._FIX_W, col_hs[i])
                col.show()
            else:
                col.hide()      # 列数变少时多余的列要收起来，否则残留在画面上
            lay = col.layout()
            if lay is not None:
                lay.activate()
        self._ncols = max(1, len(col_hs))
        self._apply_col_mask(total_w, full_h)
        self._content.update()
        self.update()

    def _render_cache(self):
        """布局版无需位图缓存。"""
        pass

    def _show_widgets(self):
        """布局版组件由 QVBoxLayout 统一管理。"""
        pass

    def sizeHint(self):
        return QSize(self._FIX_W,
                     getattr(self, "_full_h", self._HEAD_H + _kit_sc_b(12)))

    def _apply_col_mask(self, width, full_h):
        """短列下面那块空白要"不存在"：用窗口遮罩把它挖掉。

        不挖的话那块地方虽然是透明的，但仍属于气泡窗口，鼠标点不到底下的桌面/
        其他窗口——一块看不见却挡手的区域。遮罩用矩形并集（比实际画出来的圆角
        是超集），圆角该有的抗锯齿一点不受影响。
        单列时不设遮罩：保持和以前完全一致的行为。
        """
        try:
            from PyQt5.QtGui import QRegion
            panels = getattr(self, "_col_panels", None) or []
            if len(panels) <= 1:
                self.clearMask()
                return
            reg = QRegion()
            for x, y, w, h in panels:
                reg = reg.united(QRegion(int(x), int(y), int(w),
                                         min(int(h), int(full_h))))
            self.setMask(reg)
        except Exception:
            pass

    def paintEvent(self, event):
        """画背景板：**每列一块**，紧贴排列、各自按内容长短。

        多列时如果只画一整块大背景，短的那一列下面会拖着一大片空白（第二列只放
        一个小模块时特别明显）。所以按 `_col_panels` 一列一块地画：相邻两列边缘
        贴在一起，圆角各自成形，看起来就是"并排的两块面板"。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(28, 32, 44, 255))   # 底色完全不透明，透明度交给滑块（窗口透明度）
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        r = _kit_sc_b(9)
        panels = getattr(self, "_col_panels", None) or [(0, 0, self.width(),
                                                         self.height())]
        for x, y, w, h in panels:
            h = min(int(h), self.height())     # 折叠动画期间窗口可能更矮
            if w <= 2 or h <= 2:
                continue
            p.drawRoundedRect(QRectF(x + 1, y + 1, w - 2, h - 2), r, r)
        p.end()

    def _ui_tick(self):
        try:
            if self.isVisible():
                dt = 0.04
                for s in self._scroll_rows:
                    s.advance(dt)
                if not self._scroll_rows:
                    self._ui_timer.stop()
        except Exception:
            pass

    def _maybe_ui_timer(self):
        try:
            need = self.isVisible() and bool(self._scroll_rows)
            if need and not self._ui_timer.isActive():
                self._ui_timer.start(40)
            elif not need and self._ui_timer.isActive():
                self._ui_timer.stop()
        except Exception:
            pass

    def _on_row_link(self, url):
        try:
            if str(url).startswith("approve://"):
                title = str(url)[len("approve://"):]
                p = self._agent_map.get(title)
                if p is not None:
                    p.approve()
                    self._refresh()
                return
            import webbrowser
            webbrowser.open(str(url))
        except Exception:
            pass

    # ---------- 按钮行（聚合AI：打开/关闭悬浮窗） ----------

    def _on_btn_click(self):
        """聚合AI：直接用系统浏览器的应用窗口模式打开上次那个站点。

        原先是开/关内置 QtWebEngine 面板；内置内核是 Chromium 83 且占安装包
        四分之三，已改为系统浏览器的 --app 窗口（无地址栏/标签页，网页端
        全部功能可用），再塞进桌宠自己的宿主窗口——左边一条常驻站点栏，
        右边是网页，不互相遮挡。这里不再弹站点列表，换模型都在那条栏上。
        """
        try:
            from webchat_ui import open_webchat
            def _done(ok, err):
                # 静默失败是最难查的：点了没反应、哪一环坏了完全没痕迹
                # （用户报过"点击打开没有窗口弹出"，重启后自己好了，无据可查）。
                # 失败/超时写进 %TEMP%/oi_pet_error.log，用 pet_gravity 的统一入口。
                if not ok:
                    try:
                        import pet_gravity as _G
                        _G._error_log("[webchat] open_webchat 失败: %s" % (err or "未知原因"))
                    except Exception:
                        pass
                self._sync_btn_rows()
            open_webchat(self, finished=_done)
        except Exception as e:
            try:
                import pet_gravity as _G
                _G._error_log("[webchat] open_webchat 抛异常: %r" % (e,))
            except Exception:
                pass
            try:
                from widgets import kit as _k
                _k.warn(self, "聚合AI", "打开失败：%s" % e)
            except Exception:
                pass
        self._sync_btn_rows()

    def _btn_text(self):
        """按钮文案固定「打开」：切换站点交给网页窗口左边的侧边栏。"""
        return "打开"

    def _sync_btn_rows(self):
        """面板状态变化后刷新所有按钮行文案（点 ✕ 关闭时也调这里）。"""
        try:
            for wrap in self._row_widgets:
                if isinstance(wrap, _LBtnRow):
                    wrap._refresh_text()
        except Exception:
            pass

    # ---------- 布局版拖拽排序 ----------

    def _handle_hit(self, pos):
        """点在某一行的手柄上吗（分列之后要按行自己的左边缘算，不能用固定 x）。"""
        for wrap in self._row_widgets:
            if not _w_is_alive(wrap):
                continue
            r = self._row_rect(wrap)
            if r.contains(pos) and (pos.x() - r.left()) <= self._handle_w + 2:
                return wrap
        return None

    def _make_insert_line(self):
        if self._insert_line is None:
            self._insert_line = QWidget(self._content)
            self._insert_line.setStyleSheet("background:rgba(120,200,255,235);")
            self._insert_line.hide()
        return self._insert_line

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if event.pos().y() < self._HEAD_H:
                # 拖动气泡顶部空白区 = 移动桌宠（气泡跟随）
                self._drag_pet = {
                    "gx": event.globalPos().x() - self.pet.x(),
                    "gy": event.globalPos().y() - self.pet.y(),
                }
                self.pet.is_dragging = True
                event.accept()
                return
            wrap = self._handle_hit(event.pos())
            if wrap is not None:
                self._drag2 = {"widget": wrap,
                               "src": getattr(wrap, "_ridx", 0),
                               "mouse": event.pos()}
                if getattr(wrap, "_card", None) is not None:
                    wrap._card._dragging = True   # 高亮被拖拽的卡片（行不脱离布局）
                    wrap._card.update()
                self._make_insert_line()
                self._place_insert_line()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        d = getattr(self, "_drag_pet", None)
        if d is not None:
            # 必须走桌宠自己的边界钳制：以前这里是裸 move()，一路拖能把桌宠
            # 推到屏幕外面再也找不着（用户反馈"会被一直拖到屏幕之外去"）。
            # 直接拖桌宠本体走的就是 _clamp_to_desktop，两条路必须一致。
            p = QPoint(event.globalPos().x() - d["gx"],
                       event.globalPos().y() - d["gy"])
            try:
                p = self.pet._clamp_to_desktop(p)
            except Exception:
                pass
            self.pet.move(p)
            # 菜单展开时：菜单窗口跟随桌宠移动并按新位置重排（与直接拖动桌宠一致）
            try:
                menu = getattr(self.pet, "radial_menu", None)
                if menu is not None and menu.is_visible_state:
                    menu._reposition_geometry(menu._margin, menu._margin * 2)
                    try:
                        menu._tip.hide()
                    except Exception:
                        pass
                    if not menu._animating:
                        menu.reflow()
            except Exception:
                pass
            event.accept()
            return
        d = self._drag2
        if d is not None:
            d["mouse"] = event.pos()
            self._place_insert_line()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_drag_pet", None) is not None:
            self._drag_pet = None
            try:
                self.pet.is_dragging = False
                # 松手要走和"直接拖桌宠"完全相同的收尾：清掉旧吸附状态，
                # 试一次边缘吸附（成功的话桌宠自己会播倾倒动画）；没吸附就把
                # 倾角补回 0。少了这一步，从气泡拖到屏幕边只会停在那儿，
                # 既不吸附也保持着歪的姿态。
                self.pet.snapped_edge = None
                if not self.pet._check_edge_snap():
                    import pet_gravity as _pg
                    if abs(getattr(self.pet, "_rest_rotation", 0.0)) > 0.5 \
                            and not getattr(self.pet, "_rot_active", False):
                        self.pet._start_rot_anim(0.0, _pg.UNSNAP_ROT_DURATION)
            except Exception:
                pass
            event.accept()
            return
        d = self._drag2
        if d is not None:
            self._drag2 = None
            try:
                if self._insert_line is not None:
                    self._insert_line.hide()
                if getattr(d["widget"], "_card", None) is not None:
                    d["widget"]._card._dragging = False
                    d["widget"]._card.update()
                dst = d.get("dst", d["src"])
                if dst != d["src"]:
                    self._commit_reorder(d["src"], dst)
            except Exception:
                import traceback
                traceback.print_exc()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _place_insert_line(self):
        """根据鼠标位置计算插入目标，并显示高亮线。

        分列之后必须按**二维距离**找目标行：只看 y 的话，鼠标在第二列时会命中
        第一列同高度的那一行，插到完全不相干的位置去。
        """
        try:
            d = self._drag2
            if d is None:
                return
            mp = d["mouse"]
            src = d["src"]
            rows = [w for w in self._row_widgets if _w_is_alive(w)]
            target = -1
            best_d = None
            for i, w in enumerate(rows):
                g = self._row_rect(w)
                if g.contains(mp):
                    target = i
                    break
                c = g.center()
                # 水平方向加权：跨列的行要明显"更远"，免得贴着列缝时来回跳
                dd = abs(mp.y() - c.y()) + abs(mp.x() - c.x()) * 2
                if best_d is None or dd < best_d:
                    best_d, target = dd, i
            if target < 0:
                target = 0
            line = self._make_insert_line()
            if not rows or target == src:
                # 没有移动：不显示插入线
                line.hide()
                d["dst"] = src
                self.update()
                return
            g = self._row_rect(rows[target])
            # 向下拖：缝隙在目标行下方；向上拖：缝隙在目标行上方
            y = g.bottom() + 1 if target > src else g.top() - 2
            card_x = g.left() + self._handle_w
            card_w = max(10, g.width() - self._handle_w)
            line.setGeometry(int(card_x), int(y), int(card_w), 4)
            line.show()
            line.raise_()
            d["dst"] = target
            self.update()
        except Exception:
            import traceback
            traceback.print_exc()

    def _find_handle_row(self, pos):
        return -1


# 把右键转交气泡本体：行控件及其非交互子控件都挂上，确保模块行右键菜单
# （立即刷新 / 禁用启用 / 上移下移 / 删除）在任何子控件上都能弹出。
for _cls in (_HandleLabel, _ScrollText, _ModuleCard, _LTextRow, _LBtnRow, _LWidgetRow):
    _cls.contextMenuEvent = _forward_ctx_menu
