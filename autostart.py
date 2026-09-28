# -*- coding: utf-8 -*-
"""开机自启：注册表 `HKCU\\...\\CurrentVersion\\Run` 的读/写/自愈。

为什么用注册表 Run 而不是"启动"文件夹里放快捷方式：
- 不需要管理员权限，只影响当前用户；
- 不依赖 .lnk 文件（快捷方式被杀毒/清理工具删掉是常事）；
- 换目录、升级版本时一条 API 就能改干净。

这个模块**不依赖 Qt**，方便单独测试；注册表位置做成模块级常量，测试会把它
指到自己的沙箱键上，绝不碰真实的 Run 键。
"""
import os
import sys

# 注册表位置（测试会替换成沙箱键）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "oi桌宠"


def _winreg():
    import winreg
    return winreg


def supported():
    """只有 Windows 支持（这个项目本来也只跑 Windows）。"""
    return sys.platform == "win32"


def target_command():
    """当前这份程序开机自启该用的命令行。

    路径里有中文和空格，**必须带引号**，否则系统会把 `D:\\@AItest\\vibe` 当成
    程序名。打包版直接用 exe；源码模式用 pythonw（没有控制台黑框）跑 main.py。
    """
    if getattr(sys, "frozen", False):
        return '"%s"' % os.path.abspath(sys.executable)
    exe = os.path.abspath(sys.executable)
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = exe
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return '"%s" "%s"' % (pyw, main)


def current_command():
    """注册表里现在登记的命令行；没登记返回 ""。"""
    if not supported():
        return ""
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _typ = winreg.QueryValueEx(k, VALUE_NAME)
            return str(val or "")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def is_enabled():
    return bool(current_command())


def is_current():
    """登记的就是"现在这份程序"吗（升级/搬家之后会变 False）。"""
    cur = current_command()
    return bool(cur) and _norm(cur) == _norm(target_command())


def _norm(cmd):
    return " ".join(str(cmd or "").replace('"', "").split()).lower()


def set_enabled(on):
    """打开/关闭开机自启；返回是否成功。"""
    if not supported():
        return False
    winreg = _winreg()
    try:
        if on:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ,
                                  target_command())
            return True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except FileNotFoundError:
        return not on          # 键都不存在，等于"已经关着"
    except OSError:
        return False


def refresh():
    """自愈：开着自启、但登记的路径已经不是现在这份程序了，就悄悄改回来。

    不这么做的话，便携版换个目录、或者装了新版本之后，开机自启还指着旧路径——
    要么启动的是旧版本，要么干脆启动失败，而用户看到的选项还是"已开启"。
    返回是否做了修正。
    """
    try:
        if not supported() or not is_enabled() or is_current():
            return False
        return set_enabled(True)
    except Exception:
        return False
