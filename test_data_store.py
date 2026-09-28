# -*- coding: utf-8 -*-
"""数据层测试：原子写 + 每日滚动快照。

快照这套是 2026-09-26 那次事故的产物：当时一次误写把 pet_settings.json 和它的
.bak 一起污染了（.bak 是每次保存后立刻刷新的，防不了"内容写错"），只能从整包
备份里捞。现在每个文件每天留一份"被改之前"的副本，保留 7 天。

运行：python test_data_store.py（全程在临时目录里，不碰任何用户数据）
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_store as ds                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


SAND = tempfile.mkdtemp(prefix="oi_datastore_test_")
P = os.path.join(SAND, "todo_data.json")
SNAPD = os.path.join(SAND, ds.SNAP_DIR)


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


try:
    # ---------- 1. 原子写 ----------
    ds.write_json_path(P, {"v": 1})
    check("原子写：内容正确", load(P) == {"v": 1})
    check("原子写：没留下临时文件",
          not [n for n in os.listdir(SAND) if n.startswith(".oi_")])

    # ---------- 2. 快照：存的是"被改之前"的内容 ----------
    check("首次创建不留快照（没有旧内容可留）", not os.path.exists(SNAPD))
    ds.write_json_path(P, {"v": 2})
    snaps = sorted(os.listdir(SNAPD))
    today = datetime.date.today().strftime("%Y%m%d")
    check("第二次写才留快照，文件名带日期",
          snaps == ["todo_data.json." + today], str(snaps))
    check("快照里是写之前的内容（v1），不是刚写进去的 v2",
          load(os.path.join(SNAPD, snaps[0])) == {"v": 1})
    ds.write_json_path(P, {"v": 3})
    ds.write_json_path(P, {"v": 4})
    check("同一天写多次只留一份（不会把好快照顶掉）",
          len(os.listdir(SNAPD)) == 1
          and load(os.path.join(SNAPD, snaps[0])) == {"v": 1})
    check("当前文件是最后一次写的内容", load(P) == {"v": 4})

    # ---------- 3. 只保留最近 7 天 ----------
    for i in range(1, 11):
        day = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y%m%d")
        shutil.copy2(P, os.path.join(SNAPD, "todo_data.json." + day))
    ds._prune_snapshots(SNAPD, "todo_data.json", ds.SNAP_KEEP)
    left = sorted(os.listdir(SNAPD))
    check("清理后只剩 %d 份" % ds.SNAP_KEEP, len(left) == ds.SNAP_KEEP, str(len(left)))
    check("留下的是最近的那几天（含今天）", left[-1].endswith(today))

    # ---------- 4. 别的文件互不干扰 ----------
    Q = os.path.join(SAND, "notes_data.json")
    ds.write_json_path(Q, ["a"])
    ds.write_json_path(Q, ["b"])
    check("不同文件各留各的快照",
          os.path.exists(os.path.join(SNAPD, "notes_data.json." + today))
          and len([n for n in os.listdir(SNAPD) if n.startswith("todo_data")])
          == ds.SNAP_KEEP)

    # ---------- 5. 快照坏了也不能影响保存 ----------
    ro = os.path.join(SAND, "sub")
    os.makedirs(ro, exist_ok=True)
    R = os.path.join(ro, "x.json")
    ds.write_json_path(R, {"a": 1})
    _real_copy = shutil.copy2
    shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("模拟快照失败"))
    try:
        ds.write_json_path(R, {"a": 2})
        ok = load(R) == {"a": 2}
    finally:
        shutil.copy2 = _real_copy
    check("快照失败时照常保存（best-effort，绝不拖累写入）", ok)

    # ---------- 6. 设置保存也走快照 ----------
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pet_gravity.py"), encoding="utf-8").read()
    blk = src[src.index("def save_settings"):]
    blk = blk[:blk.index("def _diag_log")]
    check("pet_settings.json 保存前也会留快照", "snapshot(path)" in blk)
    check("快照目录已在 .gitignore 里（别把用户数据提进库）",
          "snapshots/" in open(os.path.join(os.path.dirname(
              os.path.abspath(__file__)), ".gitignore"), encoding="utf-8").read())
finally:
    shutil.rmtree(SAND, ignore_errors=True)
    print("临时目录已删除:", not os.path.exists(SAND))

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
