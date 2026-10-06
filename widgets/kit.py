# -*- coding: utf-8 -*-
"""通用组件工具包：与气泡/卡片风格统一的常用控件与布局助手。

用法（在自定义组件里）：
    from widgets import kit

    self.title = kit.lab("标题", size=18, bold=True)
    self.btn = kit.btn("开始", primary=True)
    self.sw = kit.switch(False)
    self.bar = kit.progress(60)
    self.layout = kit.col(kit.row(self.btn, self.sw),
                          kit.hsep(),
                          self.bar)

约定：
- 控件全部为深色半透明风格，与气泡卡片一致，无需额外写样式；
- 组件根控件建议用 kit.col/kit.row 组织，或直接给根设置 QVBoxLayout；
- 需要固定高度时定义类属性 FIX_H（框架优先按它排高）；
- 内容会变高（如文本换行、列表）时，框架按 sizeHint/heightForWidth 自适应，
  不要给根控件 setFixedHeight（除非你想锁死高度）。
"""

from PyQt5.QtCore import Qt, QPointF, QRect, QSize, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QAbstractButton, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QLayout,
                             QProgressBar, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

_FONT = "Microsoft YaHei"

# ---- 独立缩放机制 ----
# 全局缩放跟随 Windows 每块屏的实际缩放设置（Qt 高 DPI 支持，不强制 QT_SCALE_FACTOR）；
# 这里额外提供两套独立档位：
#   bubble_scale —— 气泡/组件/模块字体等"气泡内部"尺寸（设置窗口"气泡大小"滑动条）
#   pet_scale    —— 桌宠本体 + 径向菜单（设置窗口"桌宠大小"滑动条）
# 启动时 main 按 pet_settings.json 设置；组件用 bs()/ps() 包尺寸即可统一缩放，无遗漏。
_BUBBLE_SCALE = 1.0
_PET_SCALE = 1.0

# 【历史】这里曾经有个 UI_BASE = 1.5：所有窗口尺寸在运行时统一乘它一次
# （更早是强制 QT_SCALE_FACTOR=1.5）。屏幕缩放本来就由 Qt 高 DPI 支持处理，
# 再乘一遍就成了"放大两次"（检查更新窗 380 → 实际 855 宽）。
# v0.9.42 起这个倍率被**烘进源码数值**：文件里的数字就是最终像素，不再有全局
# 缩放；跨 DPI 交给 Qt。只有"应用默认字体"还需要它的大小，见 APP_FONT_PT。
APP_FONT_PT = 13.5


def ui(v):
    """历史遗留的恒等函数：数值本身就是最终像素。保留是因为还有一批调用点传的是
    变量（侧边栏宽度等），留着比逐个删更不容易出错。"""
    return max(1, int(round(v)))


# Bubble metrics live in one place. Values are standard-scale logical pixels;
# bubble_token() scales them together with fonts, spacing, and controls.
BUBBLE_TOKENS = {
    "width": 315,
    "row_height": 22.5,
    "title_width": 66,
    "head_height": 30,
    "handle_width": 9,
    "outer_margin": 3,
    "card_radius": 6,
    "card_margin": 7.5,
    "action_radius": 6,
    "action_height": 19.5,
    "action_padding": 12,
    "action_font": 15,
    "icon": 19.5,
    "icon_button_width": 24,
    "icon_button_height": 22.5,
    "toolbar_height": 24,
    "caption_height": 18,
}

HEALTH_TOKENS = {
    "ok": {"color": "#6fd39b", "label": "正常"},
    "loading": {"color": "#7db6ff", "label": "加载中"},
    "stale": {"color": "#f0b45f", "label": "数据过期"},
    "error": {"color": "#ff7a7a", "label": "出错"},
    "paused": {"color": "#a09fb0", "label": "已暂停"},
    "idle": {"color": "#96a7c4", "label": "空闲"},
}


def set_bubble_scale(s):
    global _BUBBLE_SCALE
    try:
        _BUBBLE_SCALE = max(0.5, min(3.0, float(s)))
    except Exception:
        _BUBBLE_SCALE = 1.0


def set_pet_scale(s):
    global _PET_SCALE
    try:
        _PET_SCALE = max(0.5, min(3.0, float(s)))
    except Exception:
        _PET_SCALE = 1.0


def bubble_scale():
    """气泡档位（设置里的 1/1.25/1.5/2），不含界面基准倍率。"""
    return _BUBBLE_SCALE


def pet_scale():
    """桌宠档位（设置里的 1/1.25/1.5/2），不含界面基准倍率。"""
    return _PET_SCALE


def bubble_k():
    """气泡实际放大系数 = 用户选的气泡档位。以前还乘一个界面基准倍率 1.5，
    那个 1.5 已烘进 BUBBLE_TOKENS 和各处 bs() 参数。"""
    return _BUBBLE_SCALE


def pet_k():
    """桌宠实际放大系数 = 用户选的桌宠档位。1.5 的界面倍率已烘进各处 ps() 参数。"""
    return _PET_SCALE


def bs(v):
    """气泡内逻辑尺寸（像素）按气泡档位 × 界面基准倍率缩放。"""
    return max(1, int(round(v * bubble_k())))


def bubble_token(name):
    """Return a scaled bubble metric so layout and widgets share one source."""
    try:
        return bs(BUBBLE_TOKENS[name])
    except KeyError:
        raise KeyError("unknown bubble token: %s" % name)


def bubble_widget_width():
    """交互组件在气泡里的实际宽度：气泡宽 - 两侧外边距 - 两侧拖动把手 - 卡片左右内边距。
    和 bubble_layout 组件行的算法一致（test_module_editor 里拿真气泡核对）；
    设置窗的测试区按这个宽度摆组件，排版才和气泡里一样。"""
    return (bubble_token("width") - 2 * bubble_token("outer_margin")
            - 2 * bubble_token("handle_width") - 2 * bs(7.5))


def health_token(kind, key="color"):
    """Return a stable color/label for a module health state."""
    item = HEALTH_TOKENS.get(str(kind or "ok"), HEALTH_TOKENS["ok"])
    return item.get(key, "")


def text_height(size=11.25):
    """Return rendered text height so row heights never clip CJK glyphs."""
    try:
        return max(bs(15), QFontMetrics(font_pt(size)).height())
    except Exception:
        return bs(15)


def row_height():
    """Standard module title/text row height, including enough CJK leading.

    Budget the card's own top/bottom margin as `2 * bs(1.5)`, not `bs(3)`:
    the card applies bs(1.5) twice and bs() rounds each call, so at bubble
    scale 1.5 that is 2+2=4 while bs(3) is only 3. That 1px shortfall meant
    the text widget's minimum height no longer fitted inside the card, the
    layout overflowed downwards and every value looked pushed down / clipped.
    """
    return max(bubble_token("row_height"), text_height(11.25) + 2 * bs(1.5))


def header_row_height(has_summary=False):
    """Height actually used by module_row; summary text may need more leading."""
    return max(row_height(), caption_height()) if has_summary else row_height()


def toolbar_height():
    return max(bubble_token("toolbar_height"),
               bubble_token("icon_button_height") + bs(1.5))


def caption_height():
    return max(bubble_token("caption_height"), text_height(11.25) + bs(4.5))


class ElidedLabel(QLabel):
    """Single-line label that never paints outside its allocated box."""

    def __init__(self, text="", parent=None):
        super().__init__(str(text), parent)
        self._full_text = str(text)
        self._color = "#e8ecf5"
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def set_text(self, text):
        self._full_text = str(text)
        self.setText(str(text))
        self.setToolTip(str(text) if text else "")
        self.update()

    def setText(self, text):
        self._full_text = str(text)
        super().setText(str(text))
        self.setToolTip(str(text) if text else "")
        self.update()

    def set_color(self, color):
        self._color = str(color)
        self.setStyleSheet("color:%s;background:transparent;" % color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QColor(self._color))
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full_text, Qt.ElideRight,
                              max(0, self.width()))
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, shown)
        p.end()


def font_pt(size):
    """气泡文字统一使用浮点 pt 缩放，避免 round 造成相邻档位比例跳变。"""
    f = QFont(_FONT)
    f.setPointSizeF(max(1.0, float(size) * bubble_k()))
    return f


def ps(v):
    """桌宠/菜单逻辑尺寸（像素）按桌宠档位 × 界面基准倍率缩放。"""
    return max(1, int(round(v * pet_k())))


def scale_qss(qss):
    """把 QSS 里所有 Npx 数值按气泡档位(bs)整体缩放：字号/内边距/圆角/
    控件尺寸/边框宽等一起变，杜绝"有的放大有的没放大"。
    组件内部的硬编码 QSS 在 setStyleSheet 时套一次即可，与 kit 规范件
    （已按 bs 生成）保持一致。"""
    if not qss:
        return qss
    try:
        import re as _re
        return _re.sub(
            r"(\d+(?:\.\d+)?)px",
            lambda m: "%dpx" % bs(float(m.group(1))), qss)
    except Exception:
        return qss


def lab(text="", size=16.5, color="#e8ecf5", bold=False, align=None, wrap=True):
    """通用文字标签。字号按气泡档位（bubble_scale）整体缩放。"""
    l = QLabel(str(text))
    f = font_pt(size)
    f.setBold(bold)
    l.setFont(f)
    l.setStyleSheet("color:%s;background:transparent;" % color)
    if align is not None:
        l.setAlignment(align)
    l.setWordWrap(wrap)
    return l


def action_qss(primary=False):
    """气泡内动作按钮统一 QSS：同高度、同圆角、同字号和内边距。"""
    return (
        "QPushButton{border:none;border-radius:%dpx;padding:0 %dpx;"
        "min-height:%dpx;font-family:%s;font-size:%dpx;%s%s}"
        "QPushButton:hover{%s}"
        "QPushButton:pressed{%s}"
        % (
            bubble_token("action_radius"), bubble_token("action_padding"),
            bubble_token("action_height"), _FONT, bubble_token("action_font"),
            "color:#ffffff;" if primary else "color:#e8ecf5;",
            ("background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
             "stop:0 #4a90e2,stop:1 #2fb8c0);") if primary
            else "background:rgba(255,255,255,35);",
            ("background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
             "stop:0 #5aa0eb,stop:1 #45c6ce);") if primary
            else "background:rgba(255,255,255,70);",
            "#3a80d0;" if primary else "background:rgba(74,144,226,120);",
        ))


def btn(text, primary=False, small=False, fixed_w=None):
    """通用动作按钮。small 参数保留给旧组件，但形状/字号统一不缩小。"""
    b = QPushButton(str(text))
    del small
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(bubble_token("action_height"))
    b.setStyleSheet(action_qss(primary))
    if fixed_w:
        b.setFixedWidth(bs(fixed_w))
    return b


def switch(checked=False, on_text="开", off_text="关"):
    """胶囊开关：用 QCheckBox 实现，勾选时变色并显示状态文字。
    字号/尺寸按气泡档位（bubble_scale）缩放。"""
    cb = QCheckBox(on_text if checked else off_text)
    cb.setChecked(checked)
    cb.setCursor(Qt.PointingHandCursor)
    cb.setStyleSheet(scale_qss(
        "QCheckBox{font-family:%s;font-size:15px;color:#c7d0e0;"
        "background:transparent;spacing:6px;}"
        "QCheckBox::indicator{width:36px;height:18px;border-radius:9px;"
        "background:rgba(255,255,255,45);}"
        "QCheckBox::indicator:checked{background:qlineargradient(x1:0,y1:0,"
        "x2:1,y2:0,stop:0 #4a90e2,stop:1 #2fb8c0);}"
        % _FONT))
    return cb


def progress(value=0, maximum=100, height=12):
    """细进度条（无文字）。高度/圆角按气泡档位缩放。"""
    p = QProgressBar()
    p.setRange(0, max(1, int(maximum)))
    p.setValue(int(value))
    p.setTextVisible(False)
    p.setFixedHeight(bs(height))
    h2 = max(1, bs(height) // 2)
    p.setStyleSheet(
        "QProgressBar{background:rgba(255,255,255,25);border:none;"
        "border-radius:%dpx;}"
        "QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #4a90e2,stop:1 #2fb8c0);border-radius:%dpx;}"
        % (h2, h2))
    return p


def hsep():
    """细横分隔线。

    1px 不乘 1.5：这条样式表是直接 setStyleSheet 下去的，而 hsep() 只用在组件里
    （气泡/测试区都标了 oi_nozoom），旧版的整树放大根本没碰它 —— 一直都是 1px 发丝线。
    """
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("background:rgba(255,255,255,35);border:none;"
                    "max-height:1px;")
    f.setFixedHeight(1)
    return f


def row(*items, spacing=6, margins=(0, 0, 0, 0)):
    """横向布局助手：逻辑间距/边距按气泡档位统一缩放。"""
    lay = QHBoxLayout()
    lay.setSpacing(bs(spacing))
    lay.setContentsMargins(*(bs(v) for v in margins))
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it, 0, Qt.AlignVCenter)
        elif isinstance(it, QLayout):
            # 接受任意布局（含 QGridLayout）：此前只认 QHBox/QVBox，
            # 计算器等用 QGridLayout 的组件会被静默丢弃，导致按钮全不显示。
            lay.addLayout(it)
    return lay


def col(*items, spacing=6, margins=(0, 0, 0, 0)):
    """纵向布局助手：逻辑间距/边距按气泡档位统一缩放。"""
    lay = QVBoxLayout()
    lay.setSpacing(bs(spacing))
    lay.setContentsMargins(*(bs(v) for v in margins))
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it)
        elif isinstance(it, QLayout):
            # 接受任意布局（含 QGridLayout）：此前只认 QHBox/QVBox，
            # 计算器等用 QGridLayout 的组件会被静默丢弃，导致按钮全不显示。
            lay.addLayout(it)
    return lay


def scroll(body, max_h=180):
    """透明滚动容器：内容超高时出现细滚动条，用于列表/日志等。高度按气泡档位缩放。"""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFixedHeight(bs(max_h))
    sa.setFrameShape(QFrame.NoFrame)
    sa.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    sa.setWidget(body)
    return sa


# ==================== 模块行规范件（4.6.1 可展开模块布局规范） ====================

_TITLE7_W = 66   # 模块行标题固定列宽（与其他模块对齐）


class ScrollLabel(QLabel):
    """标题标签：装不下就横向滚动，而不是被裁掉半个字。

    模块行标题是固定 44px 列（各模块要对齐），"Token 消耗"这类长标题就会被
    裁成"Token 消"。这里保持 QLabel 的完整 API（setText/text/setToolTip 都照旧），
    只在放不下时改成滚动字幕。

    所有实例共用一个类级定时器：单个标签各起一个 QTimer 会让待机白白多出
    几十次/秒的唤醒；这里只在"确实有标签需要滚"时才跑。
    """

    _SPEED = 34.0        # px/s，比气泡值区慢一些，标题不抢注意力
    _GAP = 18            # 一轮结束到下一轮开始的空白
    _PAUSE = 1.2         # 每轮开头停顿几秒，方便看清开头
    _timer = None
    _live = []           # 存的是 weakref，不是控件本身（见下）

    def __init__(self, text="", parent=None):
        super().__init__(str(text), parent)
        self._off = 0.0
        self._t_last = None
        self._color = "#96a7c4"      # 滚动时手绘用；样式表的颜色取不到
        # 必须用弱引用：气泡每次刷新都会重建模块行，强引用会把历来所有标题标签
        # 全留住（内存只涨不降）。也**不能**写 __del__ —— 在 Qt 析构期回调
        # Python 代码会踩到已经释放的 C++ 对象，实测直接 segfault。
        import weakref
        ScrollLabel._live.append(weakref.ref(self))

    def set_color(self, color):
        self._color = str(color)

    # ---- 是否需要滚 ----
    def _text_w(self):
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    def needs_scroll(self):
        return self._text_w() > self.width() + 1

    # ---- 共享定时器 ----
    @classmethod
    def _ensure_timer(cls):
        if cls._timer is not None:
            return
        from PyQt5.QtCore import QTimer
        cls._timer = QTimer()
        cls._timer.setInterval(33)          # 30fps，字幕足够顺
        cls._timer.timeout.connect(cls._tick_all)

    @classmethod
    def _tick_all(cls):
        import time as _t
        now = _t.monotonic()
        alive, active = [], 0
        for ref in cls._live:
            w = ref()
            if w is None:
                continue                    # Python 对象已回收
            try:
                visible = w.isVisible()     # C++ 对象已析构的话在这里抛
            except RuntimeError:
                continue
            alive.append(ref)
            if visible and w.needs_scroll():
                active += 1
                w._advance(now)
            else:
                w._off = 0.0
                w._t_last = None
        cls._live = alive
        if not active and cls._timer is not None:
            cls._timer.stop()

    def _advance(self, now):
        if self._t_last is None:
            self._t_last = now
            return
        dt = max(0.0, min(0.2, now - self._t_last))
        self._t_last = now
        span = self._text_w() + bs(self._GAP)
        # 开头停顿：把 _off 停在 0 附近一会儿
        self._off += self._SPEED * bubble_k() * dt
        if self._off > span:
            self._off = -self._PAUSE * self._SPEED * bubble_k()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.needs_scroll():
            ScrollLabel._ensure_timer()
            if not ScrollLabel._timer.isActive():
                ScrollLabel._timer.start()

    def setText(self, text):
        super().setText(text)
        self._off = 0.0
        self._t_last = None
        if self.isVisible() and self.needs_scroll():
            ScrollLabel._ensure_timer()
            if not ScrollLabel._timer.isActive():
                ScrollLabel._timer.start()

    def paintEvent(self, event):
        if not self.needs_scroll():
            super().paintEvent(event)       # 放得下就走 QLabel 原生绘制
            return
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(QColor(self._color))
        p.setClipRect(self.rect())
        tw = self._text_w()
        span = tw + bs(self._GAP)
        x = -max(0.0, self._off)
        r = QRect(int(x), 0, tw, self.height())
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, self.text())
        # 第二份跟在后面，滚到尾时无缝接上
        p.drawText(QRect(int(x + span), 0, tw, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()



def title7(text="", color="#96a7c4"):
    """模块行小标题：7.5pt 不加粗、固定 44px 列、左对齐垂直居中。
    与天气/聚合AI 等所有模块行标题一致（避免字号/对齐偏差）。
    字号/列宽按气泡档位（bubble_scale）缩放。
    标题比这一列宽时改为横向滚动（"Token 消耗"以前会被裁成"Token 消"）。"""
    l = ScrollLabel(str(text))
    f = QFont(_FONT)
    f.setPointSizeF(11.25 * bubble_k())
    l.setFont(f)
    l.setFixedWidth(bs(_TITLE7_W))
    l.setFixedHeight(row_height())
    l.setStyleSheet("color:%s;background:transparent;" % color)
    l.set_color(color)
    l.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    return l


def caption(text="", color="#6f7d96", wrap=False):
    """Small module text with the same type scale and clipping rules."""
    if wrap:
        l = lab(text, size=11.25, color=color, wrap=True)
        l.setMinimumHeight(caption_height())
        l.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        return l
    l = ElidedLabel(text)
    l.setFont(font_pt(11.25))
    l.set_color(color)
    l.setStyleSheet("color:%s;background:transparent;" % color)
    l.setFixedHeight(caption_height())
    return l


def module_row(title="", summary=None, actions=()):
    """Create the canonical compact module header.

    All module headers should use this: fixed left title column, optional
    stretching summary, and right-aligned actions sharing one row height.
    """
    host = QWidget()
    h = header_row_height(summary is not None)
    host.setFixedHeight(h)
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(bs(6))
    title_label = title7(title)
    lay.addWidget(title_label, 0, Qt.AlignVCenter)

    if summary is None:
        summary_label = None
        lay.addStretch(1)
    elif isinstance(summary, str):
        summary_label = caption(summary, "#7db6ff")
        lay.addWidget(summary_label, 1, Qt.AlignVCenter)
    else:
        summary_label = summary
        lay.addWidget(summary_label, 1, Qt.AlignVCenter)

    for action in actions:
        lay.addWidget(action, 0, Qt.AlignRight | Qt.AlignVCenter)
    return host, lay, title_label, summary_label


def icon_button(parent=None):
    """Uniform square-ish frame for icon-only tools."""
    from PyQt5.QtCore import QSize
    b = QPushButton("", parent)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(bubble_token("icon_button_width"),
                   bubble_token("icon_button_height"))
    b.setIconSize(QSize(bubble_token("icon"), bubble_token("icon")))
    return b


def expand_btn(text="展开", fixed_w=None):
    """展开/打开/收起等主操作按钮标准样式。"""
    return btn(text, primary=True, fixed_w=fixed_w)


def ghost_btn(text="", fixed_w=27, fixed_h=19.5, tip=""):
    """透明辅助按钮：与主按钮同行高同圆角，仅弱化底色。"""
    b = QPushButton(str(text))
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(bs(fixed_w),
                   max(bubble_token("action_height"), bs(fixed_h)))
    if tip:
        b.setToolTip(tip)
    b.setStyleSheet(
        "QPushButton{border:none;border-radius:%dpx;font-family:%s;"
        "font-size:%dpx;color:#cfe0ff;background:transparent;}"
        "QPushButton:hover{background:rgba(74,144,226,140);color:#ffffff;}"
        "QPushButton:pressed{background:#3a80d0;}"
        % (bs(6), _FONT, bs(15)))
    return b


# ==================== 画笔调色板（画布 / 拼豆共用一份） ====================
# 两边各存一份的时候，画布只有 9 色、拼豆 11 色，看起来像两个不同的产品。
# 值是拼豆那一套（用户认可的），画布跟过来——色值完全相同，不涉及存档迁移。
PALETTE = ["#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c", "#38d9a9",
           "#4dabf7", "#9775fa", "#f783ac", "#ffffff", "#808080",
           "#111111"]


# ==================== 悬停提示（全局唯一一套） ====================
# 提示框的样式**只准在这里定义一处**。以前设置窗一套深色、画布和另一个弹窗各自
# 一套浅色、气泡里的模块行又什么都没写（于是吃系统调色板 ToolTipBase #ffffdc 那块
# 黄底）——同一个气泡里悬停标题和悬停按钮能弹出两种长相。main.py 把它挂到
# QApplication 上，所有窗口继承；谁都不要再写自己的 QToolTip 规则。
TOOLTIP_QSS = ("QToolTip{background:#232a3a;color:#d5dbe8;"
               "border:2px solid #4a5468;border-radius:6px;padding:4px 9px;}")


# ==================== 弹窗按钮规范（QMessageBox / QInputDialog 通用） ====================# 统一形状（边框+圆角+底色）、适中尺寸。全局弹窗（main.py 的 QApplication QSS）与
# kit.confirm / 各组件弹窗都用它，避免"有字没形状 / 忽大忽小"。
DIALOG_BTN_QSS = (
    "QPushButton{min-width:78px;min-height:32px;font-size:15px;"
    "border:2px solid rgba(110,126,150,150);border-radius:6px;padding:0 18px;"
    "background:#ffffff;color:#2a3140;}"
    "QPushButton:hover{background:#eaf1fb;border-color:#4a90e2;color:#2a3140;}"
    "QPushButton:pressed{background:#d5dbe8;}"
    "QPushButton:default{background:#4a90e2;border-color:#4a90e2;color:#ffffff;}"
    "QPushButton:default:hover{background:#5aa0eb;}"
    "QPushButton:default:pressed{background:#3a80d0;}")


# ==================== 统一深色窗口（无边框 + 自绘标题栏）====================
# 为什么不用系统标题栏：多显示器混合 DPI 下
# Windows 会对原生窗口框做缩放虚拟化（标题栏发白、标题像被选中、跨屏后关闭按钮
# 消失），且 Qt 跨屏重建原生窗口会丢掉 DWM 暗色属性。自绘标题栏彻底规避，并与
# 桌宠/气泡/径向菜单等既有的无边框自绘窗口保持同一风格。
# 放在 kit 里是为了让 pet_gravity 与 widgets/ 下的组件共用同一实现。

class TitleIconButton(QAbstractButton):
    """标题栏方形图标按钮（关闭 ✕ / 帮助 ？），完全自绘。

    故意不用 QPushButton：父窗口（如设置窗）的样式表里有
    `QPushButton{padding:…; min-width:…}` 之类规则会层叠进来，把按钮撑成
    竖条/横条。QAbstractButton 不匹配 QPushButton 选择器，尺寸只由自己决定。
    """

    def __init__(self, kind, parent=None, size=36):
        super().__init__(parent)
        self._kind = kind            # "close" | "help"
        self._side = int(size)
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self._side, self._side)

    def sizeHint(self):
        return QSize(self._side, self._side)

    def minimumSizeHint(self):
        return self.sizeHint()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            w = h = min(self.width(), self.height())
            if self._hover or self.isDown():
                if self._kind == "close":
                    bg = QColor("#a93226") if self.isDown() else QColor("#c0392b")
                else:
                    bg = QColor(255, 255, 255, 60 if self.isDown() else 40)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(0, 0, w, h, 5, 5)
            fg = QColor("#ffffff") if self._hover else QColor("#aab3c5")
            cx, cy = w / 2.0, h / 2.0
            if self._kind == "close":
                d = w * 0.18
                p.setPen(QPen(fg, max(1.4, w * 0.055), Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
                p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
            else:
                f = QFont(_FONT)
                f.setPixelSize(int(h * 0.45))
                p.setFont(f)
                p.setPen(fg)
                p.drawText(self.rect(), Qt.AlignCenter, "?")
        finally:
            p.end()


class DarkTitleBar(QWidget):
    """自绘标题栏：标题 + 可选「？」帮助按钮 + 关闭按钮，按住可拖动窗口。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)   # 对 12px 标题字号足够；过高会在内容上方留空带
        self.setObjectName("dlgTitleBar")
        self.setStyleSheet(
            "#dlgTitleBar{background:#1b2130;border-bottom:2px solid #39414f;}")
        self._drag_off = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(15, 0, 3, 0)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignVCenter)
        self.title_lab = QLabel(str(title), self)
        self.title_lab.setStyleSheet(
            "color:#d5dbe8;font-family:'Microsoft YaHei';font-size:18px;"
            "background:transparent;")
        lay.addWidget(self.title_lab, 1)
        self.help_btn = TitleIconButton("help", self)
        self.help_btn.hide()
        lay.addWidget(self.help_btn)
        self.close_btn = TitleIconButton("close", self)
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self._on_close)
        lay.addWidget(self.close_btn)

    def _on_close(self):
        w = self.window()
        if hasattr(w, "reject"):
            w.reject()
        else:
            w.close()

    # ---- 拖动窗口 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_off = (event.globalPos()
                              - self.window().frameGeometry().topLeft())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and (event.buttons() & Qt.LeftButton):
            self.window().move(event.globalPos() - self._drag_off)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_off = None
        super().mouseReleaseEvent(event)


class DarkDialog(QDialog):
    """无边框深色对话框基类。子类把内容放进 `self.body`（而不是 self）。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        super().setWindowTitle(str(title))
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(2, 2, 2, 2)
        self._outer.setSpacing(0)
        self.title_bar = DarkTitleBar(title, self)
        self._outer.addWidget(self.title_bar)
        self.body = QWidget(self)
        self._outer.addWidget(self.body, 1)
        self.setStyleSheet("QDialog{background:#1e2430;border:2px solid #39414f;}")

    def setWindowTitle(self, title):
        super().setWindowTitle(str(title))
        try:
            self.title_bar.title_lab.setText(str(title))
        except Exception:
            pass

    def showEvent(self, event):
        """兜底：任何深色弹窗显示时都夹进屏幕，绝不半个身子在屏幕外。

        `_MsgDialog` 会另外把自己挪到操作区附近（见 place_near）；这里只保证
        "整块可见"这条底线，不改变别的弹窗原本的位置策略。
        """
        # 首次显示前把样式表再下发一次：子控件多是"构造完样式表之后"才建的，
        # 直接显示会让 QSS 里的字号/尺寸对它们不生效（见 restyle 的说明）。
        if not self.property("oiRestyled"):
            self.setProperty("oiRestyled", True)
            restyle(self)
        super().showEvent(event)
        # polish 完才轮到调色板：此时才能把输入框的占位提示色钉上去（见 dark_placeholder）
        dark_placeholder(self)
        try:
            from PyQt5.QtWidgets import QApplication
            g = self.frameGeometry()
            scr = QApplication.screenAt(g.center()) or QApplication.primaryScreen()
            av = scr.availableGeometry()
            if av.contains(g):
                return
            w = max(g.width(), self.width())
            h = max(g.height(), self.height())
            x = max(av.left(), min(g.left(), av.left() + av.width() - w))
            y = max(av.top(), min(g.top(), av.top() + av.height() - h))
            self.move(int(x), int(y))
        except Exception:
            pass

    def add_help_button(self, callback, tip=""):
        """在标题栏显示「？」按钮（替代系统标题栏的 WhatsThis 按钮）。"""
        try:
            self.title_bar.help_btn.setToolTip(tip or "帮助")
            self.title_bar.help_btn.clicked.connect(callback)
            self.title_bar.help_btn.show()
        except Exception:
            pass

    def keyPressEvent(self, event):
        # 无边框窗口也要能用 Esc 关闭
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class _MsgDialog(DarkDialog):
    """统一的深色提示/确认弹窗（替代 QMessageBox，风格与其他窗口一致）。"""

    def __init__(self, parent, title, text, confirm_mode=False, danger=False):
        super().__init__(title, parent if isinstance(parent, QWidget) else None)
        # 置顶：否则可能被始终置顶的气泡/桌宠窗口盖住，看起来像"弹窗消失了"
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self._anchor = parent if isinstance(parent, QWidget) else None
        self._ok = False
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 21, 24, 18)
        lay.setSpacing(18)
        msg = QLabel(str(text), self.body)
        msg.setWordWrap(True)
        msg.setStyleSheet(
            "color:#d5dbe8;font-family:'Microsoft YaHei';font-size:18px;"
            "background:transparent;")
        msg.setMinimumWidth(390)
        lay.addWidget(msg, 1)
        row = QHBoxLayout()
        row.addStretch()
        _qss_main = (
            "QPushButton{min-width:96px;min-height:36px;font-size:18px;"
            "border:2px solid %s;border-radius:6px;padding:3px 21px;"
            "background:%s;color:#ffffff;}"
            "QPushButton:hover{background:%s;}")
        if confirm_mode:
            no = QPushButton("取消", self.body)
            no.setCursor(Qt.PointingHandCursor)
            no.setStyleSheet(
                "QPushButton{min-width:96px;min-height:36px;font-size:18px;"
                "border:2px solid #4a5468;border-radius:6px;padding:3px 21px;"
                "background:#2b3446;color:#d5dbe8;}"
                "QPushButton:hover{background:#39414f;}")
            no.clicked.connect(self.reject)
            row.addWidget(no)
            yes = QPushButton("确定", self.body)
            yes.setCursor(Qt.PointingHandCursor)
            yes.setDefault(True)
            yes.setStyleSheet(_qss_main % (
                ("#a93226", "#c0392b", "#d04a3b") if danger
                else ("#2a8fc0", "#4a90e2", "#5aa0eb")))
            yes.clicked.connect(self._accept)
            row.addWidget(yes)
        else:
            ok = QPushButton("知道了", self.body)
            ok.setCursor(Qt.PointingHandCursor)
            ok.setDefault(True)
            ok.setStyleSheet(_qss_main % ("#2a8fc0", "#4a90e2", "#5aa0eb"))
            ok.clicked.connect(self._accept)
            row.addWidget(ok)
        lay.addLayout(row)

    def _accept(self):
        self._ok = True
        self.accept()

    def showEvent(self, event):
        # 摆位必须在这里做，不能在构造后立刻做：弹窗要等 Polish 事件才按
        # 应用字体/尺寸设置之后才算得准，构造完那会儿量到的是旧的
        # 的尺寸，照那个尺寸算出来的居中位置是偏的。
        super().showEvent(event)
        place_near(self, self._anchor)

    def result_ok(self):
        return self._ok


def restyle(w):
    """把 w 所在窗口的样式表**重新下发一次**，强制晚建的子控件重新解析样式。

    为什么需要：控件创建过程中如果调用了 ensurePolished()，而那时它还没挂进布局，
    Qt 解析不到祖先窗口的样式表；之后再挂上去也不会重新解析（已处于 polished 状态），
    于是 QSS 里声明的字号/尺寸对它不生效。旧版靠 Polish 事件里的整树放大顺带做到了
    这件事，那套删除后必须显式补一次。

    注意：这里修不了输入框占位提示的颜色。QPalette.PlaceholderText 是 Qt 从解析好的
    Text 颜色派生的，而 Text 要等控件 polish 完才是 QSS 里的值，重下样式表不改变
    这个顺序。占位色由 dark_placeholder() 显式指定，见那里的说明。
    """
    try:
        d = w.window() if hasattr(w, "window") else None
        if d is None:
            return
        ss = d.styleSheet()
        if ss:
            d.setStyleSheet(ss)
        # 重下样式表会把晚建控件的调色板整份换掉，占位色得跟着补一次
        dark_placeholder(d)
    except Exception:
        pass


def dark_placeholder(dlg, color=None):
    """把窗口里每个输入框的占位提示色，按它自己解析后的文字色 ×50% 透明钉上去。

    为什么不能靠 Qt 自己派生：QPalette.PlaceholderText 是从**解析好的** Text 色推出来
    的，而 QSS 里的 `color:` 要等控件 polish 之后才落到 Text 上。旧版整树放大的那套
    代码顺带让调色板早早解析过一轮，占位色跟着对了；那套删掉以后，Qt 推导时 Text 还是
    默认黑色，于是占位提示变成"黑色 50%"——深色底上几乎看不见
    （"留空 = 已到点"这类提示就是这么糊掉的）。

    为什么逐个子控件设、而不是设在窗口上：QSS 给每个子控件下发的是**整份**调色板，
    会把从父窗口继承来的角色一起盖掉。设在子控件自己身上才留得住。

    为什么按各自的 Text 取色、不写死一个值：同一套深色样式里不同窗口的输入框文字色
    并不一样（设置窗 #dfe6f2、AI 设置窗 #d5dbe8），写死一个就会有一个窗口偏色。
    color 不为 None 时按它取，仅给"文字色还没解析出来"的兜底场景用。
    """
    try:
        from PyQt5.QtGui import QColor, QPalette
        from PyQt5.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QTextEdit
        targets = []
        for cls in (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox):
            targets.extend(dlg.findChildren(cls))
        for w in targets:
            pal = w.palette()
            base = QColor(color) if color else pal.color(QPalette.Active, QPalette.Text)
            if not base.isValid() or (base.red() == 0 and base.green() == 0
                                      and base.blue() == 0):
                continue          # 文字色还没解析（纯黑=默认值），这轮先不动
            c = QColor(base)
            c.setAlpha(128)
            if pal.color(QPalette.Active, QPalette.PlaceholderText) == c:
                continue                      # 已经对了，别反复 setPalette
            for grp in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                pal.setColor(grp, QPalette.PlaceholderText, c)
            w.setPalette(pal)
    except Exception:
        pass


def place_near(dlg, anchor=None):
    """把弹窗摆在操作区附近，并**保证整块都在屏幕内**。

    以前 `_show_msg` 只做"居中到父窗口"，没有任何屏幕钳制：气泡贴在屏幕左边时，
    以气泡里的组件为中心一摆，弹窗就有一半跑到屏幕外（用户反馈"清空的提示窗
    飞到屏幕外了，有一半看不到"）。

    `anchor` 是发起操作的控件；取不到就用鼠标位置——总之落在用户正在看的地方，
    而不是主屏中央。
    """
    from PyQt5.QtGui import QCursor
    from PyQt5.QtWidgets import QApplication
    try:
        if isinstance(anchor, QWidget) and anchor.isVisible():
            c = anchor.mapToGlobal(anchor.rect().center())
        else:
            c = QCursor.pos()
        scr = QApplication.screenAt(c) or QApplication.primaryScreen()
        av = scr.availableGeometry()
        g = dlg.frameGeometry()
        # 与 size() 取大：窗口还没显示过时 frameGeometry 会比实际小 1px
        # （实测 320x180 的窗口报 319x179），夹完正好露出一条边
        w, h = max(g.width(), dlg.width()), max(g.height(), dlg.height())
        # 直接算坐标，不用 QRect.moveRight/moveBottom：那两个按"右边界是
        # 最后一个像素"的含义算，和 availableGeometry().right() 一起用差 1px，
        # 夹完还是会露出一条边（实测贴右下角时正好多出 1px）。
        # max 放在外层：窗口比屏幕还大时以左上为准，至少标题栏和按钮能点到。
        x = max(av.left(), min(c.x() - w // 2, av.left() + av.width() - w))
        y = max(av.top(), min(c.y() - h // 2, av.top() + av.height() - h))
        dlg.move(int(x), int(y))
    except Exception:
        pass


def _show_msg(parent, title, text, confirm_mode=False, danger=False):
    dlg = _MsgDialog(parent, title, text, confirm_mode, danger)
    dlg.exec_()          # 摆位在 _MsgDialog.showEvent 里做（那时尺寸才是最终的）
    return dlg.result_ok()


def confirm(parent, title, text, danger=False):
    """统一深色确认弹窗（确定/取消）。返回 True 表示用户点了确定。

    历史上返回 QMessageBox.Yes/No，调用方多写成 `== QMessageBox.Yes`；
    现在返回布尔值，`if kit.confirm(...)` 即可。
    """
    return _show_msg(parent, title, text, confirm_mode=True, danger=danger)


def info(parent, title, text):
    """统一深色提示弹窗（只有"知道了"）。"""
    _show_msg(parent, title, text, confirm_mode=False)


def warn(parent, title, text):
    """统一深色警告弹窗（文案同 info，语义区分，便于以后加图标/配色）。"""
    _show_msg(parent, title, text, confirm_mode=False)


# ==================== 应用默认字体 ====================
# 这里曾经是一整套"运行时统一放大"：在每个控件的 Polish 事件里把样式表 px、显式字号、
# 最小/最大尺寸、布局边距、图标尺寸统一乘 1.5，并劫持 QWidget.setStyleSheet 跟着换算。
# v0.9.42 起全部删除 —— 源码里的数值就是最终像素。留下的只有"应用默认字体"：
# 没显式设字号的控件（托盘菜单、提示框、系统标准弹窗）靠它和别处保持同样大小。

import re as _re_zoom

_PX_RE = _re_zoom.compile(r"(\d+(?:\.\d+)?)px")
_QWIDGETSIZE_MAX = 16777215


DARK_MENU_QSS = (
    "QMenu{background:#232a3a;border:2px solid #4a5468;border-radius:9px;"
    "padding:4px;font-family:'Microsoft YaHei';font-size:16px;color:#d5dbe8;}"
    "QMenu::item{padding:8px 27px;border-radius:6px;margin:0 3px;}"
    "QMenu::item:selected{background:#4a90e2;color:#ffffff;}"
    "QMenu::item:disabled{color:#566070;background:transparent;}"
    "QMenu::separator{height:2px;background:#39414f;margin:4px 9px;}")


def dark_menu(parent=None):
    """统一风格的深色弹出菜单（已按界面基准倍率放大，自己标 oi_nozoom 防二次放大）。"""
    from PyQt5.QtWidgets import QMenu
    m = QMenu(parent)
    m.setProperty("oi_nozoom", True)
    m.setStyleSheet(ui_qss(DARK_MENU_QSS))
    return m


def qss_k(qss, k):
    """把样式表里的 Npx 按任意倍率 k 换算（0px 保持 0），并**总是**归一成整数。

    k==1.0 时不能直接返回原文：自缩放区的基准值写的是 4.5px 这类精确半像素，
    直接交给 Qt 会被丢弃或截断。"""
    if not qss:
        return qss
    return _PX_RE.sub(
        lambda m: "%dpx" % (0 if float(m.group(1)) == 0
                            else max(1, int(round(float(m.group(1)) * k)))),
        qss)


def ui_qss(qss):
    """普通窗口的样式表：px 即最终值，只做一次整数归一（不再放大）。

    自缩放区的基准值里有 4.5px 这类精确半像素（见文件头说明），直接交给 Qt 会被
    丢弃或截断，所以这里统一归一。"""
    return qss_k(qss, 1.0)


def ui_i(v):
    """整数尺寸（0 保持 0）。数值即最终值。"""
    return 0 if v <= 0 else max(1, int(round(v)))


def install_app_font(app):
    """程序启动时调用一次：把应用默认字体设成 APP_FONT_PT。

    旧版是"系统默认字号 × 1.5"（Windows 上 9pt → 13.5pt）。现在直接写死这个结果，
    显示大小不变，也不再有全局倍率；没指定字号的控件都继承它。
    """
    if getattr(app, "_oi_app_font_set", False):
        return
    f = app.font()
    f.setPointSizeF(APP_FONT_PT)
    app.setFont(f)
    try:
        from PyQt5.QtWidgets import QToolTip
        QToolTip.setFont(f)
    except Exception:
        pass
    app._oi_app_font_set = True


# 兼容旧名字（外部脚本/手动检查脚本还在用）
install_ui_zoom = install_app_font
