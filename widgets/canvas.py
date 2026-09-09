# -*- coding: utf-8 -*-
"""无限画布组件：内嵌气泡（4:3），中键平移 / 左键选择框选 / 画笔 / 橡皮 / 粘贴照片。

- 收起态：一行「画布」标题 + 展开按钮（15px，与其他模块行等高）；
- 展开态：画布卡片（宽=模块宽，高=宽×3/4），工具栏悬浮右上角可收起；
- 中键拖动平移画布，滚轮以鼠标为中心缩放，左键选择/框选/绘制/擦除；
- 数据自动保存 canvas_data.json（笔画 + 照片 base64 + 视图），重启恢复。

规则配置：source.ui = "canvas"。
"""
import base64
import json
import re

from PyQt5.QtCore import (QBuffer, QIODevice, QPoint, QPointF, QRect, QRectF,
                          QSize, Qt, QTimer, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QFont, QIcon, QImage, QPainter,
                         QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import (QApplication, QDialog, QGraphicsPathItem,
                             QGraphicsPixmapItem, QGraphicsScene, QGraphicsTextItem,
                             QGraphicsView, QHBoxLayout, QInputDialog, QLabel,
                             QMessageBox, QPushButton, QSlider, QVBoxLayout,
                             QWidget)

import data_store
from widgets import ModuleWidget, kit
from widgets import icons

_FIX_W = 210          # 气泡固定宽（与 bubble_ui._FIX_W 一致）
_RATIO = 3.0 / 4.0    # 画布 4:3
_CANVAS_H = int(_FIX_W * _RATIO)   # 210 * 0.75 = 157

_PALETTE = ["#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c",
            "#4dabf7", "#9775fa", "#f783ac", "#ffffff", "#111111"]

# 弹窗按钮统一规范（形状/尺寸来自 kit，确认/输入框共用）
_BTN_QSS = kit.DIALOG_BTN_QSS

_SAVE_DELAY = 1000    # 操作后防抖 1s 落盘
_UNDO_MAX = 50


# ==================== 画布元素 ====================

class _StrokeItem(QGraphicsPathItem):
    """一笔画：颜色 + 粗细。"""

    def __init__(self, path, color, width):
        super().__init__()
        self.setPath(path)   # PyQt5 构造传 path 不生效，需显式 setPath
        self.setPen(QPen(QColor(color), width,
                         Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setBrush(QColor(0, 0, 0, 0))
        self.setFlag(QGraphicsPathItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsPathItem.ItemIsMovable, True)   # 选择后可拖动移动

    def distance_from_point(self, p):
        """点到笔画路径的最短距离（逐段计算，无命中返回 None）。"""
        path = self.path()
        n = path.elementCount()
        if n < 2:
            return None
        pts = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(n)]
        best = None
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            seg = dx * dx + dy * dy
            if seg < 1e-9:
                d2 = (p.x() - x1) ** 2 + (p.y() - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((p.x() - x1) * dx + (p.y() - y1) * dy) / seg))
                px, py = x1 + t * dx, y1 + t * dy
                d2 = (p.x() - px) ** 2 + (p.y() - py) ** 2
            if best is None or d2 < best:
                best = d2
        return best


class _PhotoItem(QGraphicsPixmapItem):
    """照片：可拖动；选中后四角手柄拖拽缩放（以图片中心为轴心，保持比例）。"""

    _HANDLE = 6.0

    def __init__(self, pixmap):
        super().__init__(pixmap)
        self._orig = pixmap.copy()   # 原始图：缩放始终基于它，避免累积失真
        self.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)
        self.setFlag(QGraphicsPixmapItem.ItemIsSelectable, True)
        self._resizing = False
        self._center_scene = None    # 缩放轴心（图片中心，场景坐标）
        self._last_scene = None

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            r = self.boundingRect()
            painter.save()
            pen = QPen(QColor(120, 200, 255, 230), 1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(0, 0, 0, 0))
            painter.drawRect(r)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(120, 200, 255))
            h = self._HANDLE
            for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                painter.drawRect(QRectF(c.x() - h / 2, c.y() - h / 2, h, h))
            painter.restore()

    def _handle_at(self, pos):
        r = self.boundingRect()
        h = self._HANDLE
        for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            if QRectF(c.x() - h / 2, c.y() - h / 2, h, h).contains(pos):
                return c
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            al = self._handle_at(e.pos())
            if al is not None:
                self._resizing = True
                # 轴心 = 图片中心（场景坐标），缩放时保持不动
                self._center_scene = self.mapToScene(self.boundingRect().center())
                self._last_scene = e.scenePos()
                self.setSelected(True)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            w0, h0 = self._orig.width(), self._orig.height()
            cur = e.scenePos()
            # 以中心为轴：拖拽点到中心的距离决定半宽/半高
            hw = abs(cur.x() - self._center_scene.x())
            hh = abs(cur.y() - self._center_scene.y())
            w1 = max(8, hw * 2)
            h1 = max(8, hh * 2)
            ratio = h0 / max(1.0, w0)
            # 保持比例：取两者中更大的缩放
            w1 = max(w1, h1 / ratio)
            h1 = w1 * ratio
            pm = self._orig.scaled(max(2, int(w1)), max(2, int(h1)),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pm)
            # 中心保持不动：pos = 中心 - 新图半宽/半高
            nw, nh = pm.width(), pm.height()
            self.setPos(self._center_scene.x() - nw / 2,
                        self._center_scene.y() - nh / 2)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = False
        super().mouseReleaseEvent(e)


# ==================== 画布视图 ====================

class CanvasView(QGraphicsView):
    """无限画布：中键平移 / 滚轮缩放 / 左键（选择框选或画笔擦除）。"""

    stroke_created = pyqtSignal(object)   # _StrokeItem 已入场景
    item_erased = pyqtSignal(object)      # item 已从场景移除
    zoomed = pyqtSignal()                 # 缩放后（刷新状态栏）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        # 无限画布：sceneRect 设极大，滚动条范围不再被笔迹包围盒限制，
        # 中键才能自由平移任意距离
        self._scene.setSceneRect(-500000, -500000, 1000000, 1000000)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        # 背景：深色 + 淡网格线（16px 一格，QBrush 平铺随视图无限延伸/缩放）
        self._grid = QPixmap(16, 16)
        self._grid.fill(QColor(24, 28, 38))
        _gp = QPainter(self._grid)
        _gp.setPen(QColor(255, 255, 255, 16))
        _gp.drawLine(0, 15, 15, 15)
        _gp.drawLine(15, 0, 15, 15)
        _gp.end()
        self.setBackgroundBrush(QBrush(self._grid))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panning = False
        self._pan_last = None
        self._tool = "select"
        self._drawing = False
        self._cur_path = None
        self._cur_item = None
        self.pen_color = _PALETTE[0]
        self.pen_width = 3
        self._apply_tool_mode()

    # ---------- 工具模式 ----------
    def set_tool(self, t):
        self._tool = t
        self._drawing = False
        self._cur_item = None
        self._apply_tool_mode()

    def _apply_tool_mode(self):
        if self._tool == "select":
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
            self.setCursor(Qt.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor if self._tool == "pen"
                           else Qt.PointingHandCursor)

    # ---------- 视图 ----------
    def view_scale(self):
        t = self.transform()
        return t.m11() if t.m11() > 0 else 1.0

    def zoom_at(self, factor, center=None):
        if center is None:
            center = self.viewport().rect().center()
        cur = self.view_scale()
        new = max(0.1, min(5.0, cur * factor))
        self.scale(new / cur, new / cur)
        self.zoomed.emit()

    def fit_contents(self):
        r = self._scene.itemsBoundingRect()
        if r.isNull() or r.width() < 1 or r.height() < 1:
            self.resetTransform()
            self.centerOn(0, 0)
            self.zoomed.emit()
            return
        self.fitInView(r.adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)
        self.zoomed.emit()

    # ---------- 鼠标 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_last = e.pos()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._tool == "pen":
            p = self.mapToScene(e.pos())
            self._cur_path = QPainterPath(p)
            self._cur_item = _StrokeItem(self._cur_path, self.pen_color,
                                         self.pen_width)
            self._scene.addItem(self._cur_item)
            self._drawing = True
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._tool == "erase":
            self._erase_at(e.pos())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning and (e.buttons() & Qt.MiddleButton):
            d = e.pos() - self._pan_last
            self._pan_last = e.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - d.y())
            e.accept()
            return
        if self._drawing and self._tool == "pen" and self._cur_item is not None:
            p = self.mapToScene(e.pos())
            self._cur_path.lineTo(p)
            self._cur_item.setPath(self._cur_path)
            e.accept()
            return
        if self._tool == "erase" and (e.buttons() & Qt.LeftButton):
            self._erase_at(e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._drawing:
            self._drawing = False
            it = self._cur_item
            self._cur_item = None
            if it is not None:
                self.stroke_created.emit(it)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps != 0:
            self.zoom_at(1.1 ** steps, e.pos())
            e.accept()
            return
        super().wheelEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.fit_contents()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def _erase_at(self, view_pos):
        p = self.mapToScene(view_pos)
        # 先精确命中（含文字）；笔画是细线，再按形状距离扩大命中（容忍 6px）
        for it in self._scene.items(p):
            if it is self._cur_item:
                continue
            if isinstance(it, (_StrokeItem, _PhotoItem, QGraphicsTextItem)):
                self._scene.removeItem(it)
                self.item_erased.emit(it)
                return
        # 细线未直接命中：按 6px 容差擦除最近笔画
        best = None
        best_d = 6.0 * 6.0
        for it in self._scene.items():
            if it is self._cur_item or not isinstance(it, _StrokeItem):
                continue
            d = it.distance_from_point(p)
            if d is not None and d <= best_d:
                best = it
                best_d = d
        if best is not None:
            self._scene.removeItem(best)
            self.item_erased.emit(best)


# ==================== 组件 ====================

class Widget(ModuleWidget):
    """气泡内无限画布：收起 = 单行按钮；展开 = 4:3 画布。"""

    FIX_H = 15
    _QSS = (
        "QToolTip{background:rgba(255,255,255,230);color:#333333;"
        "border:1px solid rgba(187,187,187,160);border-radius:3px;"
        "padding:3px 6px;font-family:SimHei;font-size:8pt;}"
        "QPushButton{border:none;border-radius:3px;padding:0 4px;"
        "font-family:SimHei;font-size:10px;color:#dbe3f0;background:transparent;}"
        "QPushButton:hover{background:rgba(74,144,226,120);color:#ffffff;}"
        "QPushButton:pressed{background:#3a80d0;color:#ffffff;}"
        "QPushButton:checked{background:rgba(74,144,226,170);color:#ffffff;}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = True    # 默认收起：单行按钮，点展开进入画布
        self._strokes = []        # 画布上的 _StrokeItem
        self._photos = []         # 画布上的 _PhotoItem
        self._ai_texts = []       # AI 绘画产生的文字图元
        self._undo_stack = []     # 操作记录
        self._redo_stack = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save)
        self.setStyleSheet(kit.scale_qss(self._QSS))

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._fold_btn = kit.expand_btn("展开")   # 渐变蓝胶囊按钮
        self._fold_btn.clicked.connect(self._toggle_fold)
        # 标题/摘要/动作统一走模块行规范，避免每个组件手写一行布局。
        self._row, rl, self._title, _summary = kit.module_row(
            "画布", actions=(self._fold_btn,))
        self._root.addWidget(self._row)

        # ---- 展开区 ----
        self._expand = QWidget(self)
        el = QVBoxLayout(self._expand)
        el.setContentsMargins(0, 2, 0, 0)
        el.setSpacing(2)
        self._view = CanvasView(self._expand)
        self._view.stroke_created.connect(self._on_stroke_created)
        self._view.item_erased.connect(self._on_item_erased)
        self._view.zoomed.connect(self._refresh_status)
        el.addWidget(self._view, 1)
        self._status = kit.caption("100%")
        el.addWidget(self._status)
        self._root.addWidget(self._expand)
        self._expand.hide()
        self.setFixedHeight(self.current_height())   # 初始收起：逻辑 15px

        # ---- 悬浮工具栏（两行紧凑：工具行 + 颜色/粗细行，可整体收起） ----
        self._bar = QWidget(self._expand)
        self._bar.setStyleSheet(kit.scale_qss(
            "QWidget#canvasBar{background:rgba(20,24,34,220);"
            "border:1px solid rgba(255,255,255,45);border-radius:5px;}"))
        self._bar.setObjectName("canvasBar")
        self._bar_v = QVBoxLayout(self._bar)
        self._bar_v.setContentsMargins(3, 2, 3, 2)
        self._bar_v.setSpacing(2)

        # 行1：工具（选择/画笔/橡皮）+ 动作（撤销/重做/粘贴/删除/清空）
        self._bar_row1 = QWidget(self._bar)
        r1 = QHBoxLayout(self._bar_row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(1)
        self._bar_row1_lay = r1
        self._bar_v.addWidget(self._bar_row1)

        # 行2：颜色色块 + 粗细滑条
        self._bar_row2 = QWidget(self._bar)
        r2 = QHBoxLayout(self._bar_row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)
        self._bar_row2_lay = r2
        self._bar_v.addWidget(self._bar_row2)

        self._build_bar()
        self._bar_visible = True

        # 收纳后的三角按钮：独立悬浮在右上角，点它还原工具栏（与展开按钮同尺寸同风格）
        self._bar_tab = QPushButton("", self._expand)
        self._bar_tab.setToolTip("展开工具栏")
        self._bar_tab.setCursor(Qt.PointingHandCursor)
        self._bar_tab.setFixedSize(kit.bubble_token("icon_button_width") + kit.bs(2),
                                   kit.bubble_token("icon_button_height"))
        self._bar_tab.setIcon(self._pix_icon("unfold"))
        self._bar_tab.setIconSize(QSize(kit.bubble_token("icon"),
                                        kit.bubble_token("icon")))
        self._bar_tab.setStyleSheet(kit.scale_qss(
            "QPushButton{border:1px solid rgba(255,255,255,45);border-radius:4px;"
            "font-family:SimHei;font-size:10px;color:#e8ecf5;"
            "background:rgba(20,24,34,220);}"
            "QPushButton:hover{background:rgba(74,144,226,140);}"))
        self._bar_tab.clicked.connect(self._toggle_bar)
        self._bar_tab.hide()

        # 画布内容变化（含选中移动/缩放）→ 防抖保存
        try:
            self._view._scene.changed.connect(self._on_scene_changed)
        except Exception:
            pass
        self._load()
        self._refresh_status()

    # ---------- 工具栏 ----------
    def _b(self, text, tip, checkable=False, icon=None):
        b = kit.icon_button(self._bar_row1)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        if icon is not None:
            b.setIcon(icon)
            b.setIconSize(QSize(13, 13))
            b.setText("")
        self._bar_row1_lay.addWidget(b)
        return b

    @staticmethod
    def _pix_icon(kind):
        """Shared high-resolution icon set."""
        return icons.icon(kind)

    def _build_bar(self):
        ic = self._pix_icon
        self._btn_select = self._b("", "选择/框选 (V)", True, ic("arrow"))
        self._btn_pen = self._b("", "画笔 (P)", True, ic("pen"))
        self._btn_erase = self._b("", "橡皮擦 (E)", True, ic("eraser"))
        self._btn_select.setChecked(True)
        self._btn_select.clicked.connect(lambda: self._set_tool("select"))
        self._btn_pen.clicked.connect(lambda: self._set_tool("pen"))
        self._btn_erase.clicked.connect(lambda: self._set_tool("erase"))
        # 动作（全部统一图标大小）
        self._btn_undo = self._b("", "撤销 (Ctrl+Z)", False, ic("undo"))
        self._btn_redo = self._b("", "重做 (Ctrl+Y)", False, ic("redo"))
        self._btn_paste = self._b("", "粘贴照片 (Ctrl+V)", False, ic("image"))
        self._btn_del = self._b("", "删除选中 (Del)", False, ic("del"))
        self._btn_clear = self._b("", "清空画布", False, ic("trash"))
        self._btn_undo.clicked.connect(self._undo)
        self._btn_redo.clicked.connect(self._redo)
        self._btn_paste.clicked.connect(self._paste)
        self._btn_del.clicked.connect(self._delete_selected)
        self._btn_clear.clicked.connect(self._clear_all)
        self._btn_ai = self._b("", "AI 绘画 (Ctrl+A)", False, ic("ai"))
        self._btn_ai.clicked.connect(self._ai_draw)
        # 收纳工具栏：收起为右上角小三角按钮，再点还原（展开朝右/收纳朝左，风格一致）
        self._btn_bar_fold = self._b("", "收纳工具栏", False, ic("fold"))
        self._btn_bar_fold.clicked.connect(self._toggle_bar)
        # 颜色（小色块）
        self._color_btns = []
        for c in _PALETTE:
            cb = QPushButton("", self._bar_row2)
            cb.setToolTip("画笔颜色")
            cb.setFixedSize(kit.bs(9), kit.bs(9))
            cb.setCursor(Qt.PointingHandCursor)
            cb.setStyleSheet(kit.scale_qss(
                "QPushButton{border:1px solid rgba(255,255,255,70);"
                "border-radius:4px;background:%s;}" % c))
            cb.clicked.connect(lambda _=False, cc=c: self._set_color(cc))
            self._color_btns.append(cb)
            self._bar_row2_lay.addWidget(cb)
        # 粗细滑条（加宽保证可见可拖）
        self._width_slider = QSlider(Qt.Horizontal, self._bar_row2)
        self._width_slider.setRange(1, 12)
        self._width_slider.setValue(3)
        self._width_slider.setFixedWidth(kit.bs(52))
        self._width_slider.setFixedHeight(kit.bs(12))
        self._width_slider.setToolTip("笔刷粗细")
        self._width_slider.setStyleSheet(kit.scale_qss(
            "QSlider::groove:horizontal{height:3px;background:rgba(255,255,255,50);"
            "border-radius:1px;}"
            "QSlider::handle:horizontal{width:8px;height:10px;margin:-4px 0;"
            "border-radius:4px;background:#7db6ff;}"))
        self._width_slider.valueChanged.connect(self._set_width)
        self._bar_row2_lay.addWidget(self._width_slider)

    def _set_tool(self, t):
        self._tool = t
        self._btn_select.setChecked(t == "select")
        self._btn_pen.setChecked(t == "pen")
        self._btn_erase.setChecked(t == "erase")
        self._view.set_tool(t)

    def _set_color(self, c):
        self._color = c
        self._view.pen_color = c
        for cb, cc in zip(self._color_btns, _PALETTE):
            cb.setStyleSheet(kit.scale_qss(
                "QPushButton{border:2px solid %s;border-radius:5px;background:%s;}"
                % ("#ffffff" if cc == c else "rgba(255,255,255,70)", cc)))
        # 有选中元素时：改其颜色（笔画改色 / 文字改色；照片无颜色属性）
        try:
            changed = False
            for it in self._view._scene.selectedItems():
                if isinstance(it, _StrokeItem):
                    pen = it.pen()
                    pen.setColor(QColor(c))
                    it.setPen(pen)
                    changed = True
                elif isinstance(it, QGraphicsTextItem):
                    it.setDefaultTextColor(QColor(c))
                    changed = True
            if changed:
                self._schedule_save()
        except Exception:
            pass

    def _set_width(self, v):
        self._width = max(1, int(v))
        self._view.pen_width = self._width

    def _toggle_bar(self):
        self._bar_visible = not self._bar_visible
        self._bar.setVisible(self._bar_visible)
        self._bar_tab.setVisible(not self._bar_visible)
        if self._bar_visible:
            self._place_bar()
        else:
            self._place_bar_tab()

    # ---------- 收起/展开 ----------
    def current_height(self):
        if self._collapsed:
            return kit.row_height()
        return (kit.row_height() + kit.bs(2) + kit.bs(_CANVAS_H)
                + kit.bs(2) + kit.caption_height())

    def _toggle_fold(self):
        self._collapsed = not self._collapsed
        self._fold_btn.setText("展开" if self._collapsed else "收起")
        # 标题行（画布 + 展开/收起按钮）始终显示；只切换画布区
        self._expand.setVisible(not self._collapsed)
        self.setFixedHeight(self.current_height())
        if not self._collapsed:
            self._place_bar()
            self._place_bar_tab()
            self._refresh_status()
        if getattr(self, "on_resize", None):
            try:
                self.on_resize()
            except Exception:
                pass

    def _place_bar(self):
        w = self._expand.width()
        self._bar.adjustSize()
        self._bar.move(max(2, w - self._bar.width() - 4), 4)
        self._bar.raise_()

    def _place_bar_tab(self):
        w = self._expand.width()
        self._bar_tab.move(max(2, w - self._bar_tab.width() - 4), 4)
        self._bar_tab.raise_()

    # ---------- 画布事件 ----------
    def _on_stroke_created(self, item):
        self._strokes.append(item)
        self._push({"kind": "add_stroke", "data": self._stroke_dict(item)})
        self._schedule_save()

    def _on_item_erased(self, item):
        if isinstance(item, _StrokeItem):
            if item in self._strokes:
                self._strokes.remove(item)
                self._push({"kind": "del_stroke", "data": self._stroke_dict(item)})
        elif isinstance(item, _PhotoItem):
            if item in self._photos:
                self._photos.remove(item)
                self._push({"kind": "del_photo", "data": self._photo_dict(item)})
        elif isinstance(item, QGraphicsTextItem):
            if item in self._ai_texts:
                self._ai_texts.remove(item)
                self._push({"kind": "del_text", "data": self._text_dict(item)})
        self._schedule_save()

    def _text_dict(self, it):
        return {"text": it.toPlainText(),
                "color": it.defaultTextColor().name(),
                "size": it.font().pixelSize(),
                "x": it.x(), "y": it.y()}

    def _build_text(self, d):
        ti = QGraphicsTextItem(str(d.get("text", "")))
        ti.setDefaultTextColor(QColor(d.get("color", "#ffffff")))
        f = QFont("SimHei")
        f.setPixelSize(max(6, int(d.get("size", 8) or 8)))
        ti.setFont(f)
        ti.setPos(d.get("x", 0), d.get("y", 0))
        ti.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
        ti.setFlag(QGraphicsTextItem.ItemIsMovable, True)
        return ti

    # ---------- 序列化 ----------
    def _stroke_dict(self, it):
        pts = []
        p = it.path()
        for i in range(p.elementCount()):
            el = p.elementAt(i)
            pts.append([round(el.x, 2), round(el.y, 2)])
        return {"color": it.pen().color().name(), "width": it.pen().width(),
                "points": pts,
                "x": it.x(), "y": it.y()}   # 支持移动后保存位置

    def _photo_dict(self, it):
        pm = it.pixmap()
        img = pm.toImage()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
        buf.close()
        return {"x": it.x(), "y": it.y(),
                "w": pm.width(), "h": pm.height(), "data": b64}

    def _build_stroke(self, d):
        pts = d.get("points", [])
        if len(pts) < 2:
            return None
        path = QPainterPath(QPointF(*pts[0]))
        for x, y in pts[1:]:
            path.lineTo(x, y)
        it = _StrokeItem(path, d.get("color", "#ffffff"),
                         int(d.get("width", 3)))
        it.setPos(d.get("x", 0), d.get("y", 0))   # 恢复移动后的位置
        return it

    def _build_photo(self, d):
        try:
            img = QImage.fromData(base64.b64decode(d.get("data", "")), "PNG")
            pm = QPixmap.fromImage(img)
            if pm.isNull():
                return None
            it = _PhotoItem(pm)
            it.setPos(d.get("x", 0), d.get("y", 0))
            return it
        except Exception:
            return None

    def _serialize(self):
        t = self._view.transform()
        return {"strokes": [self._stroke_dict(s) for s in self._strokes],
                "photos": [self._photo_dict(p) for p in self._photos],
                "view": {"m11": t.m11(), "m22": t.m22(),
                         "dx": t.dx(), "dy": t.dy()}}

    def _load(self):
        try:
            d = data_store.read_dict("canvas_data.json") or {}
            for s in d.get("strokes", []):
                it = self._build_stroke(s)
                if it is not None:
                    self._view._scene.addItem(it)
                    self._strokes.append(it)
            for p in d.get("photos", []):
                it = self._build_photo(p)
                if it is not None:
                    self._view._scene.addItem(it)
                    self._photos.append(it)
            v = d.get("view") or {}
            if v.get("m11"):
                self._view.resetTransform()
                self._view.scale(v["m11"], v["m22"])
                self._view.translate(v.get("dx", 0), v.get("dy", 0))
            else:
                self._view.fit_contents()
        except Exception:
            pass

    def _save(self):
        try:
            data_store.write_json("canvas_data.json", self._serialize())
        except Exception:
            pass

    def _schedule_save(self):
        self._save_timer.start(_SAVE_DELAY)

    def _on_scene_changed(self, *a):
        """场景变化（移动/缩放/增删）：防抖保存（橡皮擦/绘制已单独入栈）。"""
        try:
            if not self._collapsed:
                self._save_timer.start(_SAVE_DELAY)
        except Exception:
            pass

    def _refresh_status(self):
        try:
            self._status.set_text("%d%%" % round(self._view.view_scale() * 100))
        except Exception:
            pass

    # ---------- 撤销/重做 ----------
    def _push(self, op):
        self._undo_stack.append(op)
        if len(self._undo_stack) > _UNDO_MAX:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._schedule_save()

    def _undo(self):
        if not self._undo_stack:
            return
        op = self._undo_stack.pop()
        kind = op["kind"]
        if kind == "add_stroke":
            it = self._strokes.pop()
            self._view._scene.removeItem(it)
            self._redo_stack.append(op)
        elif kind == "del_stroke":
            it = self._build_stroke(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._strokes.append(it)
                self._redo_stack.append({"kind": "add_stroke", "data": op["data"]})
        elif kind == "add_photo":
            it = self._photos.pop()
            self._view._scene.removeItem(it)
            self._redo_stack.append(op)
        elif kind == "del_photo":
            it = self._build_photo(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._photos.append(it)
                self._redo_stack.append({"kind": "add_photo", "data": op["data"]})
        elif kind == "add_text":
            it = self._ai_texts.pop()
            self._view._scene.removeItem(it)
            self._redo_stack.append(op)
        elif kind == "del_text":
            it = self._build_text(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._ai_texts.append(it)
                self._redo_stack.append({"kind": "add_text", "data": op["data"]})
        elif kind == "clear":
            self._restore_snapshot(op["data"])
            self._redo_stack.append(op)
        self._schedule_save()

    def _redo(self):
        if not self._redo_stack:
            return
        op = self._redo_stack.pop()
        kind = op["kind"]
        if kind == "add_stroke":
            it = self._build_stroke(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._strokes.append(it)
                self._undo_stack.append({"kind": "del_stroke", "data": op["data"]})
        elif kind == "del_stroke":
            it = self._strokes.pop()
            self._view._scene.removeItem(it)
            self._undo_stack.append({"kind": "add_stroke", "data": op["data"]})
        elif kind == "add_photo":
            it = self._build_photo(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._photos.append(it)
                self._undo_stack.append({"kind": "del_photo", "data": op["data"]})
        elif kind == "del_photo":
            it = self._photos.pop()
            self._view._scene.removeItem(it)
            self._undo_stack.append({"kind": "add_photo", "data": op["data"]})
        elif kind == "add_text":
            it = self._build_text(op["data"])
            if it is not None:
                self._view._scene.addItem(it)
                self._ai_texts.append(it)
                self._undo_stack.append({"kind": "del_text", "data": op["data"]})
        elif kind == "del_text":
            it = self._ai_texts.pop()
            self._view._scene.removeItem(it)
            self._undo_stack.append({"kind": "add_text", "data": op["data"]})
        elif kind == "clear":
            self._clear_items()
            self._undo_stack.append(op)
        self._schedule_save()

    def _clear_items(self):
        for it in list(self._strokes):
            self._view._scene.removeItem(it)
        self._strokes.clear()
        for it in list(self._photos):
            self._view._scene.removeItem(it)
        self._photos.clear()
        for it in list(self._ai_texts):
            self._view._scene.removeItem(it)
        self._ai_texts.clear()

    def _restore_snapshot(self, data):
        self._clear_items()
        for s in data.get("strokes", []):
            it = self._build_stroke(s)
            if it is not None:
                self._view._scene.addItem(it)
                self._strokes.append(it)
        for p in data.get("photos", []):
            it = self._build_photo(p)
            if it is not None:
                self._view._scene.addItem(it)
                self._photos.append(it)

    # ---------- AI 绘画 ----------
    def _ai_draw(self):
        """AI 指令绘图：弹窗输入描述 → DeepSeek 返回 JSON 图元 → 画布执行。"""
        dlg = QInputDialog(self)
        dlg.setWindowTitle("AI 绘画")
        dlg.setLabelText("描述想画的内容（如：画一只戴帽子的猫，蓝色背景）：")
        dlg.setOption(QInputDialog.UsePlainTextEditForTextInput, True)
        dlg.setStyleSheet(kit.scale_qss(_BTN_QSS))   # 按钮规范与确认弹窗统一
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        dlg.adjustSize()   # 窗口自适应内容尺寸
        ok = dlg.exec_()
        text = dlg.textValue() if ok else ""
        if not ok or not text.strip():
            return
        self._ai_draw_with(text)

    def _ai_draw_with(self, desc, silent=False):
        """按给定描述执行 AI 绘画（工具调用/按钮共用）；silent 时失败不弹窗。"""
        try:
            self._ensure_expanded()
            self._status.set_text("AI 绘制中…")
            QApplication.processEvents()
            cmds = self._ai_fetch_commands(desc)
            if not cmds:
                self._status.set_text("AI 无内容")
                if not silent:
                    QMessageBox.information(self, "AI 绘画", "AI 没有返回可绘制的内容")
                return 0
            n = self._apply_commands(cmds)
            self._schedule_save()
            self._status.set_text("AI 完成：%d 个图元" % n)
            return n
        except Exception as e:
            self._status.set_text("AI 绘画失败")
            if not silent:
                QMessageBox.warning(self, "AI 绘画", "绘画失败：%s" % e)
            return 0

    def _ensure_expanded(self):
        """工具调用时若画布处于收起态，先展开再画。"""
        try:
            if self._collapsed:
                self._toggle_fold()
        except Exception:
            pass

    def _ai_fetch_commands(self, desc):
        """调用已配置的 DeepSeek（AI助手 规则），要求返回 JSON 绘图指令。"""
        from status_monitor import _llm_json_array
        prompt = (
            "你在一个 100x100 的白色画布上作画。根据用户描述输出 JSON 数组，"
            "每个元素是一个图元："
            '{"type":"line","x1":..,"y1":..,"x2":..,"y2":..,"color":"#rrggbb","width":2}, '
            '{"type":"circle","cx":..,"cy":..,"r":..,"color":"#rrggbb","width":2}, '
            '{"type":"rect","x":..,"y":..,"w":..,"h":..,"color":"#rrggbb","width":2}, '
            '{"type":"fill","x":..,"y":..,"w":..,"h":..,"color":"#rrggbb"}, '
            '{"type":"text","x":..,"y":..,"text":"..","color":"#rrggbb","size":8}. '
            "只输出 JSON 数组，不要任何其他文字。用户描述：" + desc)
        try:
            cmds = _llm_json_array(prompt)
        except Exception as e:
            raise RuntimeError("AI 绘图失败：%s" % e)
        if not isinstance(cmds, list):
            return []
        return cmds

    def _apply_commands(self, cmds):
        """把图元指令转为画布元素（缩放 0-100 坐标到画布区域）。"""
        count = 0
        # 内容包围盒 → 居中放到画布中心（以当前视图中心为锚）
        base = self._view.mapToScene(self._view.viewport().rect().center())
        ox, oy = base.x() - 50, base.y() - 50
        for c in cmds:
            try:
                t = str(c.get("type", ""))
                color = str(c.get("color", "#ffffff"))
                width = int(c.get("width", 2) or 2)
                if t == "line":
                    path = QPainterPath(QPointF(ox + float(c["x1"]), oy + float(c["y1"])))
                    path.lineTo(ox + float(c["x2"]), oy + float(c["y2"]))
                    it = _StrokeItem(path, color, width)
                    self._view._scene.addItem(it)
                    self._strokes.append(it)
                    self._push({"kind": "add_stroke", "data": self._stroke_dict(it)})
                    count += 1
                elif t == "circle":
                    cx, cy, r = float(c["cx"]), float(c["cy"]), float(c["r"])
                    path = QPainterPath()
                    path.addEllipse(QPointF(ox + cx, oy + cy), max(1, r), max(1, r))
                    it = _StrokeItem(path, color, width)
                    self._view._scene.addItem(it)
                    self._strokes.append(it)
                    self._push({"kind": "add_stroke", "data": self._stroke_dict(it)})
                    count += 1
                elif t == "rect":
                    x, y, w, h = (float(c["x"]), float(c["y"]),
                                  float(c["w"]), float(c["h"]))
                    path = QPainterPath()
                    path.addRect(ox + x, oy + y, w, h)
                    it = _StrokeItem(path, color, width)
                    self._view._scene.addItem(it)
                    self._strokes.append(it)
                    self._push({"kind": "add_stroke", "data": self._stroke_dict(it)})
                    count += 1
                elif t == "fill":
                    x, y, w, h = (float(c["x"]), float(c["y"]),
                                  float(c["w"]), float(c["h"]))
                    path = QPainterPath()
                    path.addRect(ox + x, oy + y, w, h)
                    it = _StrokeItem(path, color, 1)
                    it.setBrush(QColor(color))
                    self._view._scene.addItem(it)
                    self._strokes.append(it)
                    self._push({"kind": "add_stroke", "data": self._stroke_dict(it)})
                    count += 1
                elif t == "text":
                    ti = QGraphicsTextItem(str(c.get("text", "")))
                    ti.setDefaultTextColor(QColor(color))
                    f = QFont("SimHei")
                    f.setPixelSize(max(6, int(c.get("size", 8) or 8)))
                    ti.setFont(f)
                    ti.setPos(ox + float(c["x"]), oy + float(c["y"]))
                    ti.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
                    ti.setFlag(QGraphicsTextItem.ItemIsMovable, True)
                    self._view._scene.addItem(ti)
                    self._ai_texts.append(ti)
                    self._push({"kind": "add_text", "data": self._text_dict(ti)})
                    count += 1
            except Exception:
                continue
        self._view.fit_contents()
        return count

    # ---------- 动作 ----------
    @staticmethod
    def _confirm(title, text):
        """确认弹窗：置顶显示（避免被气泡遮挡）+ 按钮大小统一。"""
        mb = QMessageBox(QMessageBox.Question, title, text,
                         QMessageBox.Yes | QMessageBox.No)
        mb.setWindowFlags(mb.windowFlags() | Qt.WindowStaysOnTopHint)
        mb.button(QMessageBox.Yes).setText("是")
        mb.button(QMessageBox.No).setText("否")
        mb.setStyleSheet(kit.scale_qss(_BTN_QSS))
        return mb.exec_()

    def _delete_selected(self):
        items = self._view._scene.selectedItems()
        if not items:
            return
        for it in items:
            if isinstance(it, _StrokeItem):
                self._view._scene.removeItem(it)
                if it in self._strokes:
                    self._strokes.remove(it)
                    self._push({"kind": "del_stroke", "data": self._stroke_dict(it)})
            elif isinstance(it, _PhotoItem):
                self._view._scene.removeItem(it)
                if it in self._photos:
                    self._photos.remove(it)
                    self._push({"kind": "del_photo", "data": self._photo_dict(it)})
            elif isinstance(it, QGraphicsTextItem):
                self._view._scene.removeItem(it)
                if it in self._ai_texts:
                    self._ai_texts.remove(it)
                    self._push({"kind": "del_text", "data": self._text_dict(it)})
        self._schedule_save()

    def _clear_all(self):
        if not self._strokes and not self._photos and not self._ai_texts:
            return
        ret = self._confirm("清空画布", "确定清空整个画布？")
        if ret != QMessageBox.Yes:
            return
        snap = {"strokes": [self._stroke_dict(s) for s in self._strokes],
                "photos": [self._photo_dict(p) for p in self._photos]}
        self._clear_items()
        self._push({"kind": "clear", "data": snap})
        self._schedule_save()

    def _paste(self):
        cb = QApplication.clipboard()
        pm = cb.pixmap()
        if pm is None or pm.isNull():
            img = cb.image()
            if img is None or img.isNull():
                return
            pm = QPixmap.fromImage(img)
        if pm.isNull():
            return
        # 限制照片尺寸，避免超大图撑爆保存文件
        if pm.width() > 800 or pm.height() > 800:
            pm = pm.scaled(800, 800, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        center = self._view.mapToScene(self._view.viewport().rect().center())
        it = _PhotoItem(pm)
        it.setPos(center.x() - pm.width() / 2, center.y() - pm.height() / 2)
        self._view._scene.addItem(it)
        self._photos.append(it)
        self._view._scene.clearSelection()
        it.setSelected(True)
        self._push({"kind": "add_photo", "data": self._photo_dict(it)})
        self._schedule_save()

    # ---------- 键盘 ----------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_A and (e.modifiers() & Qt.ControlModifier):
            self._ai_draw()
            e.accept()
            return
        if e.key() == Qt.Key_V and (e.modifiers() & Qt.ControlModifier):
            self._paste()
            e.accept()
            return
        if e.key() == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            self._undo()
            e.accept()
            return
        if e.key() == Qt.Key_Y and (e.modifiers() & Qt.ControlModifier):
            self._redo()
            e.accept()
            return
        if e.key() == Qt.Key_P:
            self._set_tool("pen")
            e.accept()
            return
        if e.key() == Qt.Key_E:
            self._set_tool("erase")
            e.accept()
            return
        if e.key() == Qt.Key_V and not (e.modifiers() & Qt.ControlModifier):
            self._set_tool("select")
            e.accept()
            return
        if e.key() == Qt.Key_Delete:
            self._delete_selected()
            e.accept()
            return
        super().keyPressEvent(e)

    # ---------- 框架回调 ----------
    def set_rule(self, rule):
        super().set_rule(rule)
        name = str((rule or {}).get("name") or "").strip()
        if name:
            self._title.setText(name)

    def render(self, state, value):
        pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_bar()
