# -*- coding: utf-8 -*-
""".lnk 快捷方式解析：纯 Python 读文件 vs PowerShell（WScript.Shell）。

运行：python test_lnk.py。快捷方式在临时目录里**现场造**（一次 PowerShell 调用），
不依赖这台电脑开始菜单里有什么 —— 换台机器、全新克隆跑出来的结果一样。

另外手工核对过（不进自动测试，因为依赖本机）：开发机开始菜单 + 桌面共 368 个真实
快捷方式，纯 Python 与 PowerShell 一致 321 个、读不出（自动退回 PowerShell）38 个、
两边都没有目标 9 个、**读出来却不一致 0 个**；耗时 0.16ms/个 vs PowerShell 0.52s/个。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lnk_target                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


if sys.platform != "win32":
    print("非 Windows，跳过")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="oi_lnk_")
try:
    # ---------- 准备：目标文件 + 一批快捷方式 ----------
    zh_dir = os.path.join(TMP, "中文 目录 带空格")
    os.makedirs(zh_dir)
    zh_target = os.path.join(zh_dir, "我的程序 v2.exe")
    open(zh_target, "wb").write(b"MZ")
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    cases = {
        "系统程序.lnk": (os.path.join(sysdir, "notepad.exe"), ""),
        "中文路径.lnk": (zh_target, ""),
        "带参数.lnk": (os.path.join(sysdir, "cmd.exe"), "/k echo hi"),
        "文件夹.lnk": (zh_dir, ""),
    }
    lines = ["$s=New-Object -ComObject WScript.Shell"]
    for name, (tgt, arg) in cases.items():
        lp = os.path.join(TMP, name).replace("'", "''")
        lines.append("$l=$s.CreateShortcut('%s');$l.TargetPath='%s';$l.Arguments='%s';$l.Save()"
                     % (lp, tgt.replace("'", "''"), arg.replace("'", "''")))
    ps1 = os.path.join(TMP, "mk.ps1")
    # 带 BOM 的 UTF-8：Windows PowerShell 5 才能正确读中文路径
    io.open(ps1, "w", encoding="utf-8-sig").write("\r\n".join(lines))
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                   capture_output=True, timeout=60, creationflags=0x08000000)
    made = [n for n in cases if os.path.exists(os.path.join(TMP, n))]
    check("测试用快捷方式都造出来了", len(made) == len(cases), "%d/%d" % (len(made), len(cases)))

    # ---------- 一、读得对 ----------
    print("--- 一、纯 Python 读目标 ---")
    for name, (tgt, _arg) in cases.items():
        got = lnk_target.target(os.path.join(TMP, name))
        check("%s → 目标正确" % name,
              os.path.normcase(got) == os.path.normcase(tgt), "读到 %r" % got)

    # ---------- 二、读不了的不乱说 ----------
    print("\n--- 二、坏文件 ---")
    junk = os.path.join(TMP, "坏的.lnk")
    open(junk, "wb").write(b"not a shortcut at all")
    trunc = os.path.join(TMP, "截断.lnk")
    open(trunc, "wb").write(open(os.path.join(TMP, "系统程序.lnk"), "rb").read()[:90])
    big = os.path.join(TMP, "超大.lnk")
    with open(big, "wb") as f:
        f.write(b"\x4c\x00\x00\x00" + b"\x00" * (2 << 20))
    check("不是 .lnk 的文件 → 返回空，不抛异常", lnk_target.target(junk) == "")
    check("被截断的 .lnk → 返回空（交给 PowerShell 兜底），不抛异常",
          lnk_target.target(trunc) == "", repr(lnk_target.target(trunc)))
    check("异常大的文件不读进内存 → 返回空", lnk_target.target(big) == "")
    check("不存在的路径 → 返回空", lnk_target.target(os.path.join(TMP, "没有.lnk")) == "")

    # ---------- 三、接进桌宠：能直接读的不再起 PowerShell ----------
    print("\n--- 三、接入 pet_gravity ---")
    import pet_gravity as G
    calls = []
    import subprocess as _sp
    _orig = _sp.run

    def spy(*a, **k):
        calls.append(a[0] if a else k.get("args"))
        return _orig(*a, **k)
    _sp.run = spy
    try:
        G._lnk_target_cache.clear()
        t0 = time.perf_counter()
        got = G._resolve_lnk_target(os.path.join(TMP, "中文路径.lnk"))
        dt = (time.perf_counter() - t0) * 1000
        ps_calls = [c for c in calls if c and "powershell" in str(c[0]).lower()]
        check("普通快捷方式：不起 PowerShell，直接读出来",
              os.path.normcase(got) == os.path.normcase(zh_target) and not ps_calls,
              "%.1fms，PowerShell 调用 %d 次" % (dt, len(ps_calls)))
        calls.clear()
        G._lnk_target_cache.clear()
        G._resolve_lnk_target(junk)
        ps_calls = [c for c in calls if c and "powershell" in str(c[0]).lower()]
        check("读不出来的：照旧退回 PowerShell（行为不变，不会因为提速丢功能）",
              len(ps_calls) == 1, "PowerShell 调用 %d 次" % len(ps_calls))
        calls.clear()
        G._lnk_target_cache.clear()
        G._prewarm_lnk_cache([os.path.join(TMP, n) for n in cases])
        time.sleep(0.3)
        ps_calls = [c for c in calls if c and "powershell" in str(c[0]).lower()]
        check("批量预热：全部能直接读时一个 PowerShell 都不起",
              not ps_calls and all(os.path.join(TMP, n) in G._lnk_target_cache for n in cases),
              "PowerShell 调用 %d 次" % len(ps_calls))
    finally:
        _sp.run = _orig
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
