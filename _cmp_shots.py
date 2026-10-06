# -*- coding: utf-8 -*-
"""逐像素比对两批截图（改造前 / 后）。用法：python _cmp_shots.py <基准目录> <新目录>"""
import os
import sys

from PyQt5.QtGui import QImage


def bits(path):
    img = QImage(path)
    if img.isNull():
        return None, (0, 0)
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return img.constBits().asstring(img.byteCount()), (img.width(), img.height())


def main(a, b):
    names = sorted(set(os.listdir(a)) & set(os.listdir(b)))
    only_a = sorted(set(os.listdir(a)) - set(os.listdir(b)))
    only_b = sorted(set(os.listdir(b)) - set(os.listdir(a)))
    bad = []
    for n in names:
        ba, sa = bits(os.path.join(a, n))
        bb, sb = bits(os.path.join(b, n))
        if ba is None or bb is None:
            bad.append("%s 读不出来" % n)
            continue
        if sa != sb:
            bad.append("%s 尺寸变了：%d x %d → %d x %d"
                       % (n, sa[0], sa[1], sb[0], sb[1]))
            continue
        if ba == bb:
            print("  一致   %s  (%d x %d)" % (n, sa[0], sa[1]))
            continue
        # 数一下差多少像素、第一处差在哪
        n_px = sa[0] * sa[1]
        diff = sum(1 for i in range(0, len(ba), 4) if ba[i:i + 4] != bb[i:i + 4])
        first = next(i // 4 for i in range(0, len(ba), 4) if ba[i:i + 4] != bb[i:i + 4])
        bad.append("%s 有 %d/%d 像素不同（%.2f%%），第一处 (%d, %d)"
                   % (n, diff, n_px, 100.0 * diff / max(1, n_px),
                      first % sa[0], first // sa[0]))
    print("\n比对 %d 个窗口" % len(names))
    if only_a:
        print("只在基准里：%s" % "、".join(only_a))
    if only_b:
        print("只在新截图里：%s" % "、".join(only_b))
    if bad:
        print("\n不一致 %d 个：" % len(bad))
        for x in bad:
            print("  " + x)
    else:
        print("\n全部逐像素一致。")
    return 1 if (bad or only_a or only_b) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
