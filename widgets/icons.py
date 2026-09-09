# -*- coding: utf-8 -*-
"""High-resolution shared icons for compact module toolbars.

Icons are painted on a 24-unit grid at 4x resolution, then handed to QIcon.
Small buttons therefore get smooth, recognizable glyphs instead of fragile
one-off 14-pixel paintings.
"""
from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


_GRID = 24.0
_SCALE = 4


def pixmap(kind, color=QColor("#e1e6f0")):
    pm = QPixmap(int(_GRID * _SCALE), int(_GRID * _SCALE))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.scale(_SCALE, _SCALE)

    pen = QPen(color, 2.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    if kind == "arrow":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        path = QPainterPath(QPointF(6.2, 3.2))
        path.lineTo(QPointF(18.2, 11.6))
        path.lineTo(QPointF(12.4, 12.8))
        path.lineTo(QPointF(15.4, 19.6))
        path.lineTo(QPointF(12.6, 20.8))
        path.lineTo(QPointF(9.8, 13.9))
        path.lineTo(QPointF(6.2, 16.2))
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "pen":
        p.setBrush(color)
        body = QPainterPath(QPointF(18.6, 3.6))
        body.lineTo(QPointF(20.4, 5.4))
        body.lineTo(QPointF(10.2, 19.4))
        body.lineTo(QPointF(3.6, 20.4))
        body.lineTo(QPointF(4.6, 13.8))
        body.closeSubpath()
        p.drawPath(body)
        p.setPen(QPen(QColor("#26303f"), 1.5))
        p.drawLine(QPointF(14.8, 7.2), QPointF(17.4, 9.8))
        p.drawLine(QPointF(7.7, 13.9), QPointF(10.3, 16.5))

    elif kind == "eraser":
        _paint_eraser(p, color)

    elif kind == "image":
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 52))
        p.drawRoundedRect(QRectF(4.0, 5.0, 16.0, 14.0), 2.0, 2.0)
        p.drawEllipse(QRectF(7.0, 7.5, 3.0, 3.0))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(5.5, 16.5))
        path.lineTo(QPointF(10.0, 11.5))
        path.lineTo(QPointF(13.2, 14.6))
        path.lineTo(QPointF(15.5, 12.4))
        path.lineTo(QPointF(18.5, 16.5))
        p.drawPath(path)

    elif kind == "trash":
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 46))
        p.drawRoundedRect(QRectF(6.5, 8.0, 11.0, 12.0), 1.5, 1.5)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QRectF(5.0, 5.0, 14.0, 0.0).topLeft(),
                   QRectF(5.0, 5.0, 14.0, 0.0).topRight())
        p.drawLine(QPointF(10.0, 3.0), QPointF(14.0, 3.0))
        p.drawLine(QPointF(10.0, 11.0), QPointF(10.0, 17.0))
        p.drawLine(QPointF(14.0, 11.0), QPointF(14.0, 17.0))

    elif kind == "erase":
        _paint_eraser(p, color)

    elif kind == "undo":
        _arc_arrow(p, reverse=False)
    elif kind == "redo":
        _arc_arrow(p, reverse=True)

    elif kind == "del":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))

    elif kind == "fold":
        p.drawPolyline([QPointF(9.0, 5.0), QPointF(16.0, 12.0),
                        QPointF(9.0, 19.0)])
    elif kind == "unfold":
        p.drawPolyline([QPointF(15.0, 5.0), QPointF(8.0, 12.0),
                        QPointF(15.0, 19.0)])

    elif kind == "ai":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        path = QPainterPath(QPointF(11.0, 3.0))
        path.quadTo(QPointF(12.4, 9.6), QPointF(19.0, 11.0))
        path.quadTo(QPointF(12.4, 12.4), QPointF(11.0, 19.0))
        path.quadTo(QPointF(9.6, 12.4), QPointF(3.0, 11.0))
        path.quadTo(QPointF(9.6, 9.6), QPointF(11.0, 3.0))
        path.closeSubpath()
        p.drawPath(path)
        small = QPainterPath(QPointF(17.8, 15.2))
        small.quadTo(QPointF(18.4, 17.4), QPointF(20.6, 18.0))
        small.quadTo(QPointF(18.4, 18.6), QPointF(17.8, 20.8))
        small.quadTo(QPointF(17.2, 18.6), QPointF(15.0, 18.0))
        small.quadTo(QPointF(17.2, 17.4), QPointF(17.8, 15.2))
        p.drawPath(small)

    p.end()
    return pm


def _paint_eraser(p, color):
    p.save()
    p.translate(12.0, 10.5)
    p.rotate(45)
    p.setBrush(QColor(color.red(), color.green(), color.blue(), 75))
    p.drawRoundedRect(QRectF(-8.0, -4.7, 16.0, 9.4), 1.5, 1.5)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(1.5, -4.7), QPointF(1.5, 4.7))
    p.restore()
    p.drawLine(QPointF(4.5, 20.0), QPointF(19.5, 20.0))


def _arc_arrow(p, reverse=False):
    color = p.pen().color()
    path = QPainterPath()
    rect = QRectF(4.0, 6.0, 16.0, 13.0)
    if reverse:
        path.arcMoveTo(rect, 270)
        path.arcTo(rect, 270, -180)
    else:
        path.arcMoveTo(rect, 270)
        path.arcTo(rect, 270, 180)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    if reverse:
        arrow = [QPointF(19.0, 6.0), QPointF(23.0, 8.5),
                 QPointF(19.0, 11.0)]
    else:
        arrow = [QPointF(5.0, 6.0), QPointF(1.0, 8.5), QPointF(5.0, 11.0)]
    p.drawPolygon(arrow)


def icon(kind, color=QColor("#e1e6f0")):
    return QIcon(pixmap(kind, color))
