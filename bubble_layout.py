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

from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

from bubble_ui import (StatusBubble, ChatPanel, _w_is_alive, _split_links,
                       _widget_height)


def _font7():
    from widgets import kit as _kit
    f = QFont("SimHei")
    f.setPointSizeF(7.5 * _kit.bubble_scale())
    return f


def _font_h():
    """气泡基础字号的实际渲染高度（随气泡档位缩放），供所有文本行高取整，
    保证放大后文字不被裁切。"""
    try:
        from widgets import kit as _kit
        return _kit.text_height(7.5) + _kit.bs(4)
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


class _HandleLabel(QLabel):
    """左侧拖拽手柄：顶部三点 + 点击手型光标。"""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFixedWidth(_kit_sc_b(6))
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(140, 155, 185, 170))
        # 三点靠近顶部，避免高组件行的手柄显示在中间；点位随气泡档位缩放。
        dot = max(2, _kit_sc_b(3))
        half = dot / 2.0
        for k in (4.0, 6.0, 8.0):
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
        edge = max(1, _kit_sc_b(1))
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
        cl.setContentsMargins(_kit_sc_b(5), _kit_sc_b(1),
                              _kit_sc_b(5), _kit_sc_b(1))
        cl.setSpacing(0)
        self.title = QLabel("", self._card)
        from widgets import kit
        self.title.setFixedWidth(kit.bubble_token("title_width"))
        self.title.setFont(_font7())
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
        self._last_content = (str(title), str(value or ""),
                              bool(pending), str(link_href),
                              str((state or {}).get("kind", "ok")))
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
        cl.setContentsMargins(_kit_sc_b(5), _kit_sc_b(1),
                              _kit_sc_b(5), _kit_sc_b(1))
        cl.setSpacing(0)
        self.title = QLabel("", self._card)
        from widgets import kit
        self.title.setFixedWidth(kit.bubble_token("title_width"))
        self.title.setFont(_font7())
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
    """交互组件行：手柄（卡片外） + 卡片（标题栏 + 组件本体）。"""

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
        self._card_lay.setContentsMargins(_kit_sc_b(5), _kit_sc_b(1),
                                          _kit_sc_b(5), _kit_sc_b(1))
        self._card_lay.setSpacing(_kit_sc_b(2))
        # 标题栏：在卡片内顶部（对话面板有自己的标题栏，会隐藏此行）
        self._title_label = QLabel("", self._card)
        self._title_label.setFont(_font7())
        self._title_label.setStyleSheet("color:#96a7c4;background:transparent;")
        from widgets import kit
        self._title_label.setFixedHeight(kit.row_height())
        self._title_label.hide()
        self._card_lay.addWidget(self._title_label)
        self._lay.addWidget(self._card, 1)
        self._widget = None

    def set_title(self, title):
        t = str(title or "").strip()
        self._title_label.setText(t)
        self._title_label.setVisible(bool(t))

    def has_title(self):
        # 用 isHidden 而非 isVisible：布局阶段组件可能尚未显示，isVisible 会误判
        return not self._title_label.isHidden()

    def title_extra(self):
        """Exact vertical chrome added above an embedded widget."""
        if not self.has_title():
            return 0
        from widgets import kit
        m = self._card_lay.contentsMargins()
        return (kit.row_height() + self._card_lay.spacing()
                + m.top() + m.bottom())

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
        self._vbox = QVBoxLayout(self._content)
        # 右外边距 = 左外边距 + 手柄槽，使卡片左右留白相等、整体居中；顶部边距收小
        from widgets import kit
        self._out_m = kit.bubble_token("outer_margin")
        self._handle_w = kit.bubble_token("handle_width")
        self._vbox.setContentsMargins(self._out_m,
                                      max(0, self._HEAD_H - _kit_sc_b(3)),
                                      self._out_m + self._handle_w,
                                      _kit_sc_b(2))
        self._vbox.setSpacing(_kit_sc_b(1))
        self._row_widgets = []       # 当前布局中的行控件（按显示顺序）
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
        self._op_slider.setFixedSize(_kit_sc_b(42), _kit_sc_b(14))
        self._op_slider.setCursor(Qt.PointingHandCursor)
        self._op_slider.setToolTip("气泡透明度")
        self._op_slider.setStyleSheet(_kit_sc_qss(
            "QSlider::groove:horizontal{height:3px;"
            "background:rgba(255,255,255,45);border-radius:1px;}"
            "QSlider::handle:horizontal{width:7px;height:7px;margin:-3px 0;"
            "border-radius:4px;background:rgba(200,215,240,210);}"))
        self._op_slider.valueChanged.connect(self._on_bubble_opacity)
        # 内容层会盖住右上角按钮，必须把钉住/关闭按钮提到最上层
        self._pin_btn.raise_()
        self._fold_btn.raise_()
        self._op_slider.raise_()
        self._content.show()
        # 面板被 ✕ 关闭时，同步按钮行文案回"打开"
        try:
            import webchat_panel
            webchat_panel.on_panel_hidden(self._sync_btn_rows)
        except Exception:
            pass
        self._maybe_ui_timer()

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
                     - _kit_sc_b(10) - self._title_w - _kit_sc_b(2))
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
                    # 标题栏：对话面板自带标题栏，其余组件（待办/番茄/面板等）统一加
                    wrap.set_title(title
                                   if not isinstance(widget, ChatPanel) else "")
                    wrap.setFixedWidth(width - self._out_m
                                       - (self._out_m + self._handle_w))
                    # 显式指定交互行高度：对话面板等没有 sizeHint，交给布局会缩成一行
                    wh = (widget.current_height()
                          if hasattr(widget, "current_height")
                          else _widget_height(widget, width - 24))
                    wrap.setFixedHeight(max(10, int(wh))
                                        + wrap.title_extra())
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
            # 按新顺序重排布局
            while self._vbox.count():
                item = self._vbox.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
            for wrap in new_wraps:
                wrap.setParent(self._content)
                self._vbox.addWidget(wrap)
                wrap.show()   # setParent 会隐式隐藏，必须重新显示
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
                        wrap.setFixedWidth(self._FIX_W - self._out_m
                                           - (self._out_m + self._handle_w))
                        wh = (widget.current_height()
                              if hasattr(widget, "current_height")
                              else _widget_height(widget, self._FIX_W - 24))
                        target = max(10, int(wh)) + wrap.title_extra()
                        if int(wrap.minimumHeight()) != target:
                            wrap.setFixedHeight(target)
                        if wrap.has_title():
                            wrap.set_state(self._row_state(i, title))
                        wrap.show()          # 隐藏->重开时确保重新显示
                        widget.show()
                    continue
                pending = bool(title in self._pending_titles)
                link_href = ""
                p = self._agent_map.get(title)
                if p is not None and p.pending_request() and str(value or "").startswith("待审批"):
                    link_href = "approve://" + str(title)
                state = self._row_state(i, title, pending)
                sig = (str(title), str(value or ""), bool(pending), str(link_href),
                       str(state.get("kind", "ok")))
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

    def _apply_size(self, width):
        """按行高累加计算气泡高度并应用（含展开/收起动画与补位）。"""
        # 用 minimumHeight（=setFixedHeight 的权威值），sizeHint 在复用行时会返回过期缓存
        total = 0
        for wrap in self._row_widgets:
            total += max(10, int(wrap.minimumHeight()))
        total += max(0, len(self._row_widgets) - 1) * self._vbox.spacing()
        m = self._vbox.contentsMargins()
        full_h = max(self._HEAD_H + _kit_sc_b(8),
                     total + m.top() + m.bottom() + _kit_sc_b(4))
        self._full_h = full_h
        self.setFixedWidth(width)
        self._content.setGeometry(0, 0, width, full_h)
        # 右上角悬浮控件定位：钉住 / 关闭 / 透明度滑块，尺寸与间距随气泡档位一起缩放，
        # 否则按钮放大后仍按固定偏移会重叠错位。
        _btn_w = _kit_sc_b(16)
        _slider_w = _kit_sc_b(42)
        _rm = _kit_sc_b(6)      # 关闭按钮距右边缘
        _gap1 = _kit_sc_b(2)    # 钉住↔关闭 间距
        _gap2 = _kit_sc_b(4)    # 透明度↔钉住 间距
        _fold_x = width - _rm - _btn_w
        _pin_x = _fold_x - _gap1 - _btn_w
        _slider_x = _pin_x - _gap2 - _slider_w
        self._pin_btn.move(_pin_x, _kit_sc_b(1))
        self._fold_btn.move(_fold_x, _kit_sc_b(1))
        self._op_slider.move(_slider_x, _kit_sc_b(2))
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
            total = 0
            for wrap in self._row_widgets:
                total += max(10, int(wrap.minimumHeight()))
            total += max(0, len(self._row_widgets) - 1) * self._vbox.spacing()
            m = self._vbox.contentsMargins()
            full_h = max(self._HEAD_H + _kit_sc_b(8),
                         total + m.top() + m.bottom() + _kit_sc_b(4))
            self._full_h = full_h
            anchor = getattr(panel, "_fold_anchor", None)
            if anchor is None:
                anchor = self.y() + self.height()
            mode = getattr(panel, "_fold_mode", "bottom")
            fx = getattr(panel, "_fold_x", None)
            if fx is None:
                fx = self.x()
            if mode == "top":
                y = anchor
            else:
                y = anchor - full_h
            # 先定窗口几何（锚点固定），再跟内容，避免内容先移动露出窗口外造成残影
            self.setGeometry(fx, y, self._FIX_W, full_h)
            self._content.setGeometry(0, 0, self._FIX_W, full_h)
            self._content.update()
            self.update()
        except Exception:
            pass

    def _render_cache(self):
        """布局版无需位图缓存。"""
        pass

    def _show_widgets(self):
        """布局版组件由 QVBoxLayout 统一管理。"""
        pass

    def sizeHint(self):
        return QSize(self._FIX_W,
                     getattr(self, "_full_h", self._HEAD_H + _kit_sc_b(8)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(28, 32, 44, 255))   # 底色完全不透明，透明度交给滑块（窗口透明度）
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
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
        try:
            import webchat_panel
            webchat_panel.toggle_panel()
        except Exception:
            pass
        self._sync_btn_rows()

    def _btn_text(self):
        try:
            import webchat_panel
            return "关闭" if webchat_panel.is_panel_open() else "打开"
        except Exception:
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
        if pos.x() >= self._out_m + self._handle_w:
            return None
        for wrap in self._row_widgets:
            if _w_is_alive(wrap) and wrap.geometry().contains(pos):
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
                               "mouse": event.pos().y()}
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
            self.pet.move(event.globalPos().x() - d["gx"],
                          event.globalPos().y() - d["gy"])
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
            d["mouse"] = event.pos().y()
            self._place_insert_line()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_drag_pet", None) is not None:
            self._drag_pet = None
            try:
                self.pet.is_dragging = False
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
        """根据鼠标位置计算插入目标，并显示高亮线。"""
        try:
            d = self._drag2
            if d is None:
                return
            my = d["mouse"]
            src = d["src"]
            rows = [w for w in self._row_widgets if _w_is_alive(w)]
            target = -1
            for i, w in enumerate(rows):
                g = w.geometry()
                if g.top() <= my < g.bottom():
                    target = i
                    break
            if target < 0:
                if rows:
                    first = rows[0].geometry()
                    last = rows[-1].geometry()
                    if my < first.center().y():
                        target = 0
                    elif my >= last.center().y():
                        target = len(rows) - 1
                    else:
                        best = 0
                        best_d = abs(my - first.center().y())
                        for i, w in enumerate(rows):
                            dd = abs(my - w.geometry().center().y())
                            if dd < best_d:
                                best_d = dd
                                best = i
                        target = best
                else:
                    target = 0
            line = self._make_insert_line()
            if target == src:
                # 没有移动：不显示插入线
                line.hide()
                d["dst"] = src
                self.update()
                return
            g = rows[target].geometry()
            # 向下拖：缝隙在目标行下方；向上拖：缝隙在目标行上方
            y = g.bottom() + 1 if target > src else g.top() - 2
            card_x = self._out_m + self._handle_w
            card_w = self._FIX_W - 2 * (self._out_m + self._handle_w)
            line.setGeometry(card_x, int(y), card_w, 4)
            line.show()
            line.raise_()
            d["dst"] = target
            self.update()
        except Exception:
            import traceback
            traceback.print_exc()

    def _find_handle_row(self, pos):
        return -1
