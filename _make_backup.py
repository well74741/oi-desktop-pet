# -*- coding: utf-8 -*-
"""打包备份桌宠项目（排除缓存/日志/旧备份），输出到工作区。"""

import datetime
import os
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))
DST_DIR = SRC

skip_dirs = {"__pycache__", ".git", ".claude", ".codex", ".agents",
             "backups", "build", "dist"}
skip_ext = {".pyc", ".pyo", ".zip", ".log"}


def main():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(DST_DIR, "oi桌宠_backup_%s.zip" % stamp)
    count = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(SRC):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for n in sorted(names):
                if os.path.splitext(n)[1].lower() in skip_ext:
                    continue
                p = os.path.join(root, n)
                rel = os.path.relpath(p, SRC)
                z.write(p, os.path.join("oi桌宠", rel))
                count += 1
    print("backup created: %s (%d files)" % (dst, count))


if __name__ == "__main__":
    main()
