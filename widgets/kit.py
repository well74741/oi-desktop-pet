# -*- coding: utf-8 -*-
"""通用组件工具包：与气泡/卡片风格统一的常用控件与布局助手。

用法（在自定义组件里）：
    from widgets import kit

    self.title = kit.lab("标题", size=12, bold=True)
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

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

_FONT = "SimHei"

# ---- 独立缩放机制 ----
# 全局缩放走 Qt 的 QT_SCALE_FACTOR（main.py 设置，影响所有 UI）；
# 这里额外提供两套独立档位：
#   bubble_scale —— 气泡/组件/模块字体等"气泡内部"尺寸（设置窗口"气泡大小"滑动条）
#   pet_scale    —— 桌宠本体 + 径向菜单（设置窗口"桌宠大小"滑动条）
# 启动时 main 按 pet_settings.json 设置；组件用 bs()/ps() 包尺寸即可统一缩放，无遗漏。
_BUBBLE_SCALE = 1.0
_PET_SCALE = 1.0


# Bubble metrics live in one place. Values are standard-scale logical pixels;
# bubble_token() scales them together with fonts, spacing, and controls.
BUBBLE_TOKENS = {
    "width": 210,
    "row_height": 15,
    "title_width": 44,
    "head_height": 20,
    "handle_width": 6,
    "outer_margin": 2,
    "card_radius": 4,
    "card_margin": 5,
    "action_radius": 4,
    "action_height": 13,
    "action_padding": 8,
    "action_font": 10,
    "icon": 13,
    "icon_button_width": 16,
    "icon_button_height": 15,
    "toolbar_height": 16,
    "caption_height": 12,
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
    return _BUBBLE_SCALE


def pet_scale():
    return _PET_SCALE


def bs(v):
    """气泡内逻辑尺寸（像素）按气泡档位缩放。"""
    return max(1, int(round(v * _BUBBLE_SCALE)))


def bubble_token(name):
    """Return a scaled bubble metric so layout and widgets share one source."""
    try:
        return bs(BUBBLE_TOKENS[name])
    except KeyError:
        raise KeyError("unknown bubble token: %s" % name)


def health_token(kind, key="color"):
    """Return a stable color/label for a module health state."""
    item = HEALTH_TOKENS.get(str(kind or "ok"), HEALTH_TOKENS["ok"])
    return item.get(key, "")


def text_height(size=7.5):
    """Return rendered text height so row heights never clip CJK glyphs."""
    try:
        return max(bs(10), QFontMetrics(font_pt(size)).height())
    except Exception:
        return bs(10)


def row_height():
    """Standard module title/text row height, including enough CJK leading."""
    return max(bubble_token("row_height"), text_height(7.5) + bs(2))


def header_row_height(has_summary=False):
    """Height actually used by module_row; summary text may need more leading."""
    return max(row_height(), caption_height()) if has_summary else row_height()


def toolbar_height():
    return max(bubble_token("toolbar_height"),
               bubble_token("icon_button_height") + bs(1))


def caption_height():
    return max(bubble_token("caption_height"), text_height(7.5) + bs(3))


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
    f.setPointSizeF(max(1.0, float(size) * bubble_scale()))
    return f


def ps(v):
    """桌宠/菜单逻辑尺寸（像素）按桌宠档位缩放。"""
    return max(1, int(round(v * _PET_SCALE)))


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
            r"(\d+)px",
            lambda m: "%dpx" % bs(int(m.group(1))), qss)
    except Exception:
        return qss


def lab(text="", size=11, color="#e8ecf5", bold=False, align=None, wrap=True):
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
        b.setFixedWidth(bs(int(fixed_w)))
    return b


def switch(checked=False, on_text="开", off_text="关"):
    """胶囊开关：用 QCheckBox 实现，勾选时变色并显示状态文字。
    字号/尺寸按气泡档位（bubble_scale）缩放。"""
    cb = QCheckBox(on_text if checked else off_text)
    cb.setChecked(checked)
    cb.setCursor(Qt.PointingHandCursor)
    cb.setStyleSheet(scale_qss(
        "QCheckBox{font-family:%s;font-size:10px;color:#c7d0e0;"
        "background:transparent;spacing:4px;}"
        "QCheckBox::indicator{width:24px;height:12px;border-radius:6px;"
        "background:rgba(255,255,255,45);}"
        "QCheckBox::indicator:checked{background:qlineargradient(x1:0,y1:0,"
        "x2:1,y2:0,stop:0 #4a90e2,stop:1 #2fb8c0);}"
        % _FONT))
    return cb


def progress(value=0, maximum=100, height=8):
    """细进度条（无文字）。高度/圆角按气泡档位缩放。"""
    p = QProgressBar()
    p.setRange(0, max(1, int(maximum)))
    p.setValue(int(value))
    p.setTextVisible(False)
    p.setFixedHeight(bs(int(height)))
    h2 = max(1, bs(int(height)) // 2)
    p.setStyleSheet(
        "QProgressBar{background:rgba(255,255,255,25);border:none;"
        "border-radius:%dpx;}"
        "QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #4a90e2,stop:1 #2fb8c0);border-radius:%dpx;}"
        % (h2, h2))
    return p


def hsep():
    """细横分隔线。"""
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("background:rgba(255,255,255,35);border:none;"
                    "max-height:1px;")
    f.setFixedHeight(1)
    return f


def row(*items, spacing=4, margins=(0, 0, 0, 0)):
    """横向布局助手：逻辑间距/边距按气泡档位统一缩放。"""
    lay = QHBoxLayout()
    lay.setSpacing(bs(spacing))
    lay.setContentsMargins(*(bs(v) for v in margins))
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it, 0, Qt.AlignVCenter)
        elif isinstance(it, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(it)
    return lay


def col(*items, spacing=4, margins=(0, 0, 0, 0)):
    """纵向布局助手：逻辑间距/边距按气泡档位统一缩放。"""
    lay = QVBoxLayout()
    lay.setSpacing(bs(spacing))
    lay.setContentsMargins(*(bs(v) for v in margins))
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it)
        elif isinstance(it, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(it)
    return lay


def scroll(body, max_h=120):
    """透明滚动容器：内容超高时出现细滚动条，用于列表/日志等。高度按气泡档位缩放。"""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFixedHeight(bs(int(max_h)))
    sa.setFrameShape(QFrame.NoFrame)
    sa.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    sa.setWidget(body)
    return sa


# ==================== 模块行规范件（4.6.1 可展开模块布局规范） ====================

_TITLE7_W = 44   # 模块行标题固定列宽（与其他模块对齐）


def title7(text="", color="#96a7c4"):
    """模块行小标题：7.5pt 不加粗、固定 44px 列、左对齐垂直居中。
    与天气/聚合AI 等所有模块行标题一致（避免字号/对齐偏差）。
    字号/列宽按气泡档位（bubble_scale）缩放。"""
    l = QLabel(str(text))
    f = QFont(_FONT)
    f.setPointSizeF(7.5 * bubble_scale())
    l.setFont(f)
    l.setFixedWidth(bs(_TITLE7_W))
    l.setFixedHeight(row_height())
    l.setStyleSheet("color:%s;background:transparent;" % color)
    l.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    return l


def caption(text="", color="#6f7d96", wrap=False):
    """Small module text with the same type scale and clipping rules."""
    if wrap:
        l = lab(text, size=7.5, color=color, wrap=True)
        l.setMinimumHeight(caption_height())
        l.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        return l
    l = ElidedLabel(text)
    l.setFont(font_pt(7.5))
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
    lay.setSpacing(bs(4))
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


def ghost_btn(text="", fixed_w=18, fixed_h=13, tip=""):
    """透明辅助按钮：与主按钮同行高同圆角，仅弱化底色。"""
    b = QPushButton(str(text))
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(bs(int(fixed_w)),
                   max(bubble_token("action_height"), bs(int(fixed_h))))
    if tip:
        b.setToolTip(tip)
    b.setStyleSheet(
        "QPushButton{border:none;border-radius:%dpx;font-family:%s;"
        "font-size:%dpx;color:#cfe0ff;background:transparent;}"
        "QPushButton:hover{background:rgba(74,144,226,140);color:#ffffff;}"
        "QPushButton:pressed{background:#3a80d0;}"
        % (bs(4), _FONT, bs(10)))
    return b


# ==================== 弹窗按钮规范（QMessageBox / QInputDialog 通用） ====================
# 统一形状（边框+圆角+底色）、适中尺寸。全局弹窗（main.py 的 QApplication QSS）与
# kit.confirm / 各组件弹窗都用它，避免"有字没形状 / 忽大忽小"。
DIALOG_BTN_QSS = (
    "QPushButton{min-width:52px;min-height:21px;font-size:10px;"
    "border:1px solid rgba(110,126,150,150);border-radius:4px;padding:0 12px;"
    "background:#ffffff;color:#2a3140;}"
    "QPushButton:hover{background:#eaf1fb;border-color:#4a90e2;color:#2a3140;}"
    "QPushButton:pressed{background:#d5dbe8;}"
    "QPushButton:default{background:#4a90e2;border-color:#4a90e2;color:#ffffff;}"
    "QPushButton:default:hover{background:#5aa0eb;}"
    "QPushButton:default:pressed{background:#3a80d0;}")


def confirm(parent, title, text):
    """置顶确认弹窗（是/否）：WindowStaysOnTopHint 避免被气泡遮挡，
    按钮统一形状（kit.DIALOG_BTN_QSS）。返回 QMessageBox.Yes / No。
    必须把 parent 传给 QMessageBox：无父窗口的模态框可能被始终置顶的
    气泡窗口盖住，导致弹窗"消失"。"""
    from PyQt5.QtWidgets import QMessageBox
    if isinstance(parent, QWidget):
        mb = QMessageBox(QMessageBox.Question, title, text,
                         QMessageBox.Yes | QMessageBox.No, parent)
        mb.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    else:
        mb = QMessageBox(QMessageBox.Question, title, text,
                         QMessageBox.Yes | QMessageBox.No)
        mb.setWindowFlags(mb.windowFlags() | Qt.WindowStaysOnTopHint)
    mb.button(QMessageBox.Yes).setText("是")
    mb.button(QMessageBox.No).setText("否")
    mb.setStyleSheet(DIALOG_BTN_QSS)
    return mb.exec_()
