# -*- coding: utf-8 -*-
"""开机自启的读写与自愈测试。

**全程只碰自己的沙箱注册表键**（`HKCU\\Software\\oi桌宠_test_autostart`），
把 `autostart.RUN_KEY` 指过去再测，跑完删掉——绝不动真实的
`...\\CurrentVersion\\Run`，免得把用户自己的启动项改坏。

运行：python test_autostart.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autostart as A                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


if not A.supported():
    print("非 Windows，跳过")
    sys.exit(0)

import winreg                                           # noqa: E402

REAL_KEY = A.RUN_KEY
SAND_KEY = r"Software\oi桌宠_test_autostart"
A.RUN_KEY = SAND_KEY
check("隔离：测试用的是沙箱键，不是真实的 Run 键",
      A.RUN_KEY == SAND_KEY and "CurrentVersion" not in A.RUN_KEY)

try:
    # ---------- 1. 命令行拼装 ----------
    cmd = A.target_command()
    check("命令行带引号（路径里有中文和空格，不带引号会被截断）",
          cmd.startswith('"') and cmd.count('"') >= 2, cmd)
    check("命令行指向真实存在的程序",
          os.path.exists(cmd.split('"')[1]), cmd.split('"')[1])
    if not getattr(sys, "frozen", False):
        check("源码模式用 pythonw（不弹控制台黑框）",
              "pythonw" in cmd.lower() or "python" in cmd.lower())

    # ---------- 2. 开 / 关 ----------
    check("初始状态：没登记", not A.is_enabled())
    check("打开自启", A.set_enabled(True) and A.is_enabled())
    check("打开之后登记的就是现在这份程序", A.is_current())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SAND_KEY) as k:
        val, typ = winreg.QueryValueEx(k, A.VALUE_NAME)
    check("写进去的是 REG_SZ 字符串", typ == winreg.REG_SZ)
    check("写进去的内容 == target_command()", val == A.target_command())
    check("重复打开不出错（幂等）", A.set_enabled(True) and A.is_enabled())
    check("关闭自启", A.set_enabled(False) and not A.is_enabled())
    check("重复关闭不出错（幂等）", A.set_enabled(False) and not A.is_enabled())
    check("关掉之后 is_current() 是假的", not A.is_current())

    # ---------- 3. 自愈：路径过期了要改回来 ----------
    A.set_enabled(True)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SAND_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, A.VALUE_NAME, 0, winreg.REG_SZ,
                          r'"D:\旧目录\oi桌宠0.9.01.exe"')
    check("换了路径之后 is_current() 能认出来不是自己", not A.is_current())
    check("refresh() 把路径改回现在这份程序",
          A.refresh() and A.is_current() and A.is_enabled())
    check("路径本来就对时 refresh() 什么都不做", A.refresh() is False)
    A.set_enabled(False)
    check("没开自启时 refresh() 不会擅自打开",
          A.refresh() is False and not A.is_enabled())

    # ---------- 4. 键不存在时也别抛异常 ----------
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SAND_KEY)
    except OSError:
        pass
    check("键都不存在时：读返回空、关闭算成功、refresh 不动作",
          A.current_command() == "" and not A.is_enabled()
          and A.set_enabled(False) and A.refresh() is False)

    # ---------- 5. 不在 settings 里留第二份状态 ----------
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pet_gravity.py"), encoding="utf-8").read()
    check("回归：自启状态不存进 settings（两处存同一件事迟早不一致）",
          'settings["autostart"]' not in src and "'autostart'" not in src)
finally:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SAND_KEY)
    except OSError:
        pass
    A.RUN_KEY = REAL_KEY
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SAND_KEY):
            left = True
    except OSError:
        left = False
    print("沙箱键已删除:", not left)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
