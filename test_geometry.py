# -*- coding: utf-8 -*-
"""几何不变式：任何模块行、任何气泡档位下，内容都不许超出自己的框。

为什么单独立一个套件：这个项目栽在同一类 bug 上至少三次——
- v0.9.07 对话面板收起态被裁（高度写死 _BASE_H=54，加了标题栏就不够）；
- v0.9.10 模块行文字偏低出框（换微软雅黑后行高 15→20，比预算高 1px）；
- v0.9.16 之前分列时短列拖一大片空白。
共同点都是"尺寸是算出来的，但没人按真实渲染量过"。所以这里不测具体像素值，
只守两条**与字体、档位、DPI 都无关**的不变式：
  1) 每个子控件都落在父框里（不出框、不被裁）；
  2) 同一行里的标题 / 值 / 按钮共用一条中线（不错位）。
三个档位（1.0 / 1.5 / 2.0）× 所有行类型 × 所有交互组件都跑一遍。

运行：python test_geometry.py（离屏，不碰任何用户数据）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QPoint                          # noqa: E402
from PyQt5.QtGui import QFontMetrics                      # noqa: E402
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget   # noqa: E402

app = QApplication([])

from widgets import kit                                  # noqa: E402
from widgets import load_module_widget                   # noqa: E402
import bubble_layout as BL                               # noqa: E402

PASS, FAIL = [], []
BL_OUT_M = kit.bubble_token("outer_margin")
BL_HANDLE_W = kit.bubble_token("handle_width")

WIDGET_UIS = ["perler", "canvas", "tokenmeter", "stats", "todo",
              "notes", "countdown", "calc", "tomato"]
SCALES = (1.0, 1.5, 2.0)


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def host_row(row, width):
    """把行放进一个容器里跑一次真实布局，返回容器（保持引用不被回收）。"""
    h = QWidget()
    lay = QVBoxLayout(h)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(row)
    h.resize(int(width), max(40, row.minimumHeight() + 8))
    h.show()
    app.processEvents()
    h.layout().activate()
    app.processEvents()
    return h


def overflow(parent, w):
    """子控件相对父框超出了多少像素（左/上/右/下 取最大，<=0 表示没超）。"""
    tl = w.mapTo(parent, QPoint(0, 0))
    return max(-tl.x(), -tl.y(),
               (tl.x() + w.width()) - parent.width(),
               (tl.y() + w.height()) - parent.height())


def deep_overflow(row):
    """这一行里超出得最多的那个子控件 (超出像素, 名字)。"""
    worst, who = -10 ** 6, ""
    for w in row.findChildren(QWidget):
        if not w.isVisibleTo(row) or w.width() <= 0 or w.height() <= 0:
            continue
        ov = overflow(row, w)
        if ov > worst:
            worst, who = ov, w.__class__.__name__
    return worst, who


def mid(parent, w):
    return w.mapTo(parent, QPoint(0, 0)).y() + w.height() / 2.0


for sc in SCALES:
    kit.set_bubble_scale(sc)
    tag = "x%s" % sc

    # ---------- 文字行：短值 / 长值（会走滚动字幕）/ 富文本链接 / 失败状态 ----------
    for label, value, state in (
            ("短值", "48%", None),
            ("长值", "48% (30.9/63.7 GB) 还有一长串会滚动的内容在后面", None),
            ("失败", "获取失败", {"kind": "error", "error": "HTTP Error 503"}),
            ("加载", "", {"kind": "loading"})):
        row = BL._LTextRow()
        row.set_content("内存", value, kit.bs(150), state=state)
        h = host_row(row, kit.bs(210))
        ov, who = deep_overflow(row)
        check("文字行·%s(%s)：内容不出框" % (label, tag), ov <= 0,
              "超出 %dpx（%s）" % (ov, who))
        check("文字行·%s(%s)：标题与值同一条中线" % (label, tag),
              abs(mid(row, row.title) - mid(row, row.value)) <= 1.0
              if row._value_label is None else True)
        h.close()

    # ---------- 按钮行 ----------
    brow = BL._LBtnRow()
    brow.set_content("聚合AI")
    h = host_row(brow, kit.bs(210))
    ov, who = deep_overflow(brow)
    check("按钮行(%s)：内容不出框" % tag, ov <= 0, "超出 %dpx（%s）" % (ov, who))
    check("按钮行(%s)：标题与按钮同一条中线" % tag,
          abs(mid(brow, brow.title) - mid(brow, brow.btn)) <= 1.0,
          "标题 %.1f / 按钮 %.1f" % (mid(brow, brow.title), mid(brow, brow.btn)))
    h.close()

    # ---------- 交互组件行：每个组件都塞进行里量一遍 ----------
    bad_fit, bad_h = [], []
    for name in WIDGET_UIS:
        w, err = load_module_widget(name)
        if w is None:
            bad_fit.append("%s(加载失败:%s)" % (name, err))
            continue
        row = BL._LWidgetRow()
        row.set_title("测试")
        row.set_widget(w)
        # 高度按**气泡自己那套预算**来设（照抄 _relayout 的算法），这才是线上
        # 真正生效的值；行本身没有固定高度，单独 new 出来量是 0，不代表 bug
        wh = (w.current_height() if hasattr(w, "current_height")
              else BL._widget_height(w, kit.bs(210) - 24))
        row.setFixedWidth(kit.bs(210) - 2 * (BL_OUT_M + BL_HANDLE_W))
        row.setFixedHeight(max(10, int(wh)) + row.title_extra())
        h = host_row(row, kit.bs(210))
        ov, who = deep_overflow(row)
        if ov > 0:
            bad_fit.append("%s 超出 %dpx(%s)" % (name, ov, who))
        # 预算必须容得下组件自己要的高度，不能把组件压扁
        need = w.minimumHeight() if w.minimumHeight() > 0 else w.sizeHint().height()
        if int(wh) + 1 < need:
            bad_h.append("%s 预算 %d < 组件要的 %d" % (name, int(wh), need))
        h.close()
    check("组件行(%s)：%d 个组件的内容都不出框" % (tag, len(WIDGET_UIS)),
          not bad_fit, "；".join(bad_fit[:3]))
    check("组件行(%s)：气泡给的高度预算不会把组件压扁" % tag, not bad_h,
          "；".join(bad_h[:3]))

kit.set_bubble_scale(1.0)

# ---------- 标题装不下时滚动，而不是被裁掉半个字 ----------
# 模块行标题是固定 44px 列（各模块要对齐），"Token 消耗"会被裁成"Token 消"。
# 三个档位都要么放得下、要么滚动，不能出现"裁一半"。
_hosts = []
for sc in SCALES:
    kit.set_bubble_scale(sc)
    _long = kit.title7("Token 消耗")
    _short = kit.title7("CPU")
    _h = QWidget()
    _l = QVBoxLayout(_h)
    _l.setContentsMargins(0, 0, 0, 0)
    _l.addWidget(_long)
    _l.addWidget(_short)
    _h.resize(kit.bs(210), kit.row_height() * 2 + 8)
    _h.show()
    app.processEvents()
    _hosts.append(_h)
    _tw = QFontMetrics(_long.font()).horizontalAdvance("Token 消耗")
    check("滚动标题(x%s)：装不下的长标题会滚动（列 %d < 文字 %d）"
          % (sc, _long.width(), _tw),
          _long.needs_scroll() if _tw > _long.width() else True)
    check("滚动标题(x%s)：放得下的短标题不滚（不必要的动效不做）" % sc,
          not _short.needs_scroll())
    check("滚动标题(x%s)：滚动标题不改变行的高度预算" % sc,
          _long.height() == _short.height() == kit.row_height())

# 待机开销：没有任何可见的滚动标题时，共享定时器必须停下来
for _h in _hosts:
    _h.hide()
app.processEvents()
kit.ScrollLabel._tick_all()
check("滚动标题：全部隐藏后共享定时器自动停（不白占待机 CPU）",
      kit.ScrollLabel._timer is None or not kit.ScrollLabel._timer.isActive())

# 登记表必须是弱引用：气泡每次刷新都重建模块行，存强引用等于把历来所有标题
# 标签都留住（内存只涨不降）。而且**不能**加 __del__ —— 在 Qt 析构期回调
# Python 代码会踩已释放的 C++ 对象，打包用的 Python 3.12 环境实测直接 segfault，
# 是打包闸门拦下来的。
import gc                                                  # noqa: E402
import weakref                                             # noqa: E402

check("滚动标题：登记表存的是弱引用，不是控件本身",
      all(isinstance(r, weakref.ref) for r in kit.ScrollLabel._live))
check("滚动标题：没有 __del__（Qt 析构期回调 Python 会 segfault）",
      "__del__" not in kit.ScrollLabel.__dict__)
_before = len(kit.ScrollLabel._live)
_tmp_host = QWidget()
_tmp_lay = QVBoxLayout(_tmp_host)
for _i in range(30):
    _tmp_lay.addWidget(kit.title7("临时标题 %d" % _i))
_tmp_host.deleteLater()
del _tmp_host, _tmp_lay
app.processEvents()
gc.collect()
kit.ScrollLabel._tick_all()          # 这一轮会把死掉的条目清出去
check("滚动标题：控件销毁后登记表会自己收缩（不是只涨不降，%d -> %d）"
      % (_before + 30, len(kit.ScrollLabel._live)),
      len(kit.ScrollLabel._live) <= _before + 30)

kit.set_bubble_scale(1.0)

# ---------- 行高预算：与字体无关的公式不变式 ----------
# 卡片上下各调一次 bs(1)，bs() 每次取整；行高预算若用 bs(2) 会差 1px，
# 那正是 v0.9.10 "文字偏低出框" 的根因。
for sc in SCALES:
    kit.set_bubble_scale(sc)
    need = BL._font_h() + 2 * kit.bs(1)
    check("行高公式(x%s)：容得下一行字 + 卡片内边距（%d <= %d）"
          % (sc, need, kit.row_height()), need <= kit.row_height())
kit.set_bubble_scale(1.0)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
