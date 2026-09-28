# -*- coding: utf-8 -*-
"""清理 dist 里的历史产物：每类只留最近 KEEP 个版本。

`dist/` 里每发一版就多三个文件（便携 exe、安装包、源码 zip），十来个版本就
几百兆。这里按文件名里的版本号排序，只留最近的几版；**当前版本永远保留**。

用法：
    python _prune_dist.py            # 按 KEEP 清理（默认留 3 版）
    python _prune_dist.py 5          # 留 5 版
    python _prune_dist.py --dry      # 只看会删什么，不动手
build.bat 打包成功后会自动调一次。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
KEEP_DEFAULT = 3

# 每类一条正则：括号里必须是版本号
PATTERNS = [
    ("便携版", re.compile(r"^oi桌宠(\d+\.\d+\.\d+)\.exe$")),
    ("安装包", re.compile(r"^oi桌宠_Setup_v(\d+\.\d+\.\d+)\.exe$")),
    ("源码包", re.compile(r"^oi桌宠_v(\d+\.\d+\.\d+)_源码\.zip$")),
]


def _ver_key(v):
    return tuple(int(x) for x in v.split("."))


def current_version():
    try:
        sys.path.insert(0, HERE)
        import module_core
        return module_core.APP_VERSION
    except Exception:
        return None


def prune(keep=KEEP_DEFAULT, dry=False):
    if not os.path.isdir(DIST):
        print("没有 dist 目录，不用清理")
        return 0
    cur = current_version()
    names = os.listdir(DIST)
    freed, removed = 0, []
    for label, rx in PATTERNS:
        hit = []
        for n in names:
            m = rx.match(n)
            if m:
                hit.append((_ver_key(m.group(1)), m.group(1), n))
        hit.sort(reverse=True)                      # 版本号从新到旧
        # 保留集合：最近 keep 个版本 + 当前版本（当前版永远留着）
        keep_vers = {v for _k, v, _n in hit[:max(0, keep)]}
        if cur:
            keep_vers.add(cur)
        for _k, ver, n in hit:
            if ver in keep_vers:
                continue
            p = os.path.join(DIST, n)
            size = os.path.getsize(p)
            removed.append((label, n, size))
            freed += size
            if not dry:
                try:
                    os.remove(p)
                except Exception as e:
                    print("  删不掉 %s：%s" % (n, e))
    if not removed:
        print("dist 无需清理（每类都在 %d 个版本以内）" % keep)
        return 0
    for label, n, size in removed:
        print("  %s %s（%.1f MB）%s" % ("将删" if dry else "已删", n,
                                        size / 1048576.0, "" if not dry else ""))
    print("%s %d 个文件，%s %.0f MB（保留最近 %d 版 + 当前版 %s）"
          % ("可释放" if dry else "已释放", len(removed),
             "预计" if dry else "共", freed / 1048576.0, keep, cur))
    return len(removed)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    dry = "--dry" in args or "-n" in args
    nums = [a for a in args if a.isdigit()]
    prune(int(nums[0]) if nums else KEEP_DEFAULT, dry)
