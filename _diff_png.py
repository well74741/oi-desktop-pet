# -*- coding: utf-8 -*-
"""给两张截图做可视化差异：输出红色高亮的差异图 + 差异最密集区域的并排裁切。

用法：python _diff_png.py <基准.png> <新.png> <输出前缀>
"""
import sys

from PyQt5.QtGui import QColor, QImage, qAlpha, qBlue, qGreen, qRed
from PyQt5.QtWidgets import QApplication

app = QApplication([])


def load(p):
    img = QImage(p)
    if img.isNull():
        raise SystemExit("读不出：%s" % p)
    return img.convertToFormat(QImage.Format_RGBA8888)


def main(pa, pb, out):
    a, b = load(pa), load(pb)
    w, h = min(a.width(), b.width()), min(a.height(), b.height())
    print("基准 %dx%d，新 %dx%d，比对区 %dx%d"
          % (a.width(), a.height(), b.width(), b.height(), w, h))
    hi = QImage(b)
    rows = {}
    n = 0
    minx, miny, maxx, maxy = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
                rows[y] = rows.get(y, 0) + 1
                hi.setPixelColor(x, y, QColor(255, 0, 0))
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    print("不同像素 %d / %d (%.2f%%)" % (n, w * h, 100.0 * n / max(1, w * h)))
    if not n:
        return
    print("差异包围盒 x=[%d,%d] y=[%d,%d]" % (minx, maxx, miny, maxy))
    top = sorted(rows.items(), key=lambda kv: -kv[1])[:8]
    print("差异最多的行：%s" % ", ".join("y=%d(%d)" % t for t in top))
    hi.save(out + "_hi.png")
    # 包围盒裁切并排（基准在上、新的在下）
    pad = 6
    x0, y0 = max(0, minx - pad), max(0, miny - pad)
    x1, y1 = min(w, maxx + pad + 1), min(h, maxy + pad + 1)
    cw, ch = x1 - x0, y1 - y0
    comb = QImage(cw, ch * 2 + 4, QImage.Format_RGBA8888)
    comb.fill(QColor(255, 0, 255))
    for y in range(ch):
        for x in range(cw):
            comb.setPixelColor(x, y, QColor(a.pixel(x0 + x, y0 + y)))
            comb.setPixelColor(x, y + ch + 4, QColor(b.pixel(x0 + x, y0 + y)))
    comb.save(out + "_sbs.png")
    print("已写出 %s_hi.png（红色=不同）和 %s_sbs.png（上=基准 下=新）" % (out, out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
