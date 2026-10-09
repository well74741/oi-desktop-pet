# -*- coding: utf-8 -*-
"""一键跑完所有自动化测试套件，打包前的闸门。

自动发现同目录下的 `test_*.py`，各自起一个子进程跑（互相隔离，一个崩了不影响
别的），汇总通过/失败。**`_check_*.py` 不在内**：那些要起真实浏览器、需要人看
着，属于发版前的手动验证。

每个套件都在**独立的沙箱 TEMP** 里跑：桌宠用 `%TEMP%/oi_pet.lock` 做单实例锁，
不换 TEMP 的话会和用户正在运行的桌宠抢锁，测试直接静默退出。

用法：
    python run_all_tests.py          # 全跑
    python run_all_tests.py -q       # 只打汇总
退出码非 0 表示有套件失败（build.bat 据此拒绝出包）。
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QUIET = "-q" in sys.argv or "--quiet" in sys.argv

# stdout/stderr 统一 UTF-8：不然编码跟着控制台走。CI（GitHub Actions 的
# windows runner）是 cp1252，打印中文测试输出直接 UnicodeEncodeError 炸掉——
# 而且是在打印失败信息那行炸的，把真正挂掉的套件名都吞了，比挂掉本身更难查。
# 错误处理选 backslashreplace：再出问题也只丢这一行的可读性，不再中断汇总。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def suites():
    """按文件名排序的测试套件；跑得快的排前面只是为了早点看到结果。"""
    found = sorted(os.path.basename(p)
                   for p in glob.glob(os.path.join(HERE, "test_*.py")))
    return found


def run(name, sandbox):
    env = dict(os.environ)
    env["TEMP"] = env["TMP"] = sandbox
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 套件打印的是中文结果（PASS/FAIL + 名称），CI 的 cp1252 控制台编不出中文，
    # 套件会在 check() 的 print 那里直接崩——而且崩在结果行上，统计全丢。
    # 这里给子进程钉死 UTF-8：套件自己不用管跑在哪块控制台上。
    env.setdefault("PYTHONIOENCODING", "utf-8")
    t0 = time.monotonic()
    # encoding 必须显式 utf-8：text=True 会用 locale 编码（CI 是 cp1252），
    # 子进程明明输出的是上一行钉死的 UTF-8，收回来解码成乱码，日志里就
    # 全是 ç³»ç»Ÿç¨‹åº 这种花字。errors=replace：再出问题也只丢几个字符。
    p = subprocess.run([sys.executable, "-u", os.path.join(HERE, name)],
                       cwd=HERE, env=env, capture_output=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.returncode and (p.stderr or "") or "")
    dt = time.monotonic() - t0
    # 各套件的收尾行格式不完全一样，这里只抓"通过/失败"的数字，抓不到就算 0
    tail = [ln for ln in out.splitlines() if ln.strip()][-6:]
    stat = ""
    for ln in tail:
        if "passed" in ln or "通过" in ln:
            stat = ln.strip().strip("=").strip()
            break
    return p.returncode, dt, stat, out


def main():
    names = suites()
    if not names:
        print("没找到任何 test_*.py")
        return 1
    sandbox = tempfile.mkdtemp(prefix="oi_tests_")
    rows, bad = [], []
    try:
        for n in names:
            code, dt, stat, out = run(n, sandbox)
            rows.append((n, code, dt, stat))
            if code != 0:
                bad.append(n)
                print("\n" + "=" * 60)
                print("FAIL  %s  (退出码 %d)" % (n, code))
                print("=" * 60)
                print(out[-3000:])
            elif not QUIET:
                print("PASS  %-26s %5.1fs  %s" % (n, dt, stat))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print("\n" + "-" * 60)
    total = sum(r[2] for r in rows)
    print("%d 个套件，%d 个通过，%d 个失败，共 %.1fs"
          % (len(rows), len(rows) - len(bad), len(bad), total))
    if bad:
        print("失败：" + "、".join(bad))
        print("手动验证（要起真实浏览器，不在本脚本内）："
              "_check_host.py / _check_chatpanel.py")
        return 1
    print("全部通过。手动验证项：_check_host.py（聚合AI 真实窗口）、"
          "_check_launch_cwd.py（启动外部程序不继承桌宠目录）、"
          "_check_dock_life.py（贴边栏切站点/被关掉/收起态）、"
          "_check_dock_drag.py（拖窗口时贴边栏的开销）、"
          "_check_ai_tools.py（AI 工具对话：超时/请求次数/误删防护）、"
          "_check_hotkey.py（全局热键：解析/注册/冲突/线程）、"
          "_check_hotkey_capture.py（被占用的组合也能录，真实按键）、"
          "_check_chatpanel.py（对话面板几何）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
