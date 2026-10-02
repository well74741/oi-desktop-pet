# -*- coding: utf-8 -*-
"""验真：桌宠启动外部程序时，子进程**不会**继承桌宠的工作目录。

这是"装新版报 DeleteFile failed; code 5"的真正根因：子进程继承 CWD，而 Windows
的 DLL 搜索顺序会查当前目录 —— 被启动的程序（用户实测是从径向菜单启动的
Photoshop）就从 `C:\\Program Files\\oi桌宠\\_internal\\` 里加载了
VCRUNTIME140.dll 并一直占着句柄，于是安装程序替换不了那个文件。
任务管理器里找不到桌宠也照样装不上 —— 占用者根本不是桌宠。

用法：python _check_launch_cwd.py（只起 cmd.exe，不碰任何真实程序）
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


# 模拟"桌宠正跑在安装目录里"：把 CWD 切到一个假的安装目录
fake_install = tempfile.mkdtemp(prefix="oi_fake_install_")
os.makedirs(os.path.join(fake_install, "_internal"), exist_ok=True)
real_cwd = os.getcwd()
os.chdir(fake_install)
print("假装桌宠的工作目录 =", os.getcwd())

try:
    from pet_gravity import RadialMenu
    from widgets.launcher import Widget as LauncherWidget
except Exception as e:
    print("导入失败:", e)
    os.chdir(real_cwd)
    sys.exit(1)

# ---- 1) _launch_cwd 的取值 ----
exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                   "System32", "cmd.exe")
got = RadialMenu._launch_cwd(exe)
check("径向菜单：给出的工作目录是目标程序自己的目录，不是桌宠的",
      got is not None and os.path.normcase(got) != os.path.normcase(fake_install),
      "得到 %s" % got)
check("径向菜单：就是 cmd.exe 所在的 System32",
      got is not None
      and os.path.normcase(got) == os.path.normcase(os.path.dirname(exe)))
got2 = RadialMenu._launch_cwd("notepad")      # 不是文件路径（系统命令）
check("径向菜单：命令类（notepad）退到用户主目录，也不是桌宠目录",
      got2 is not None
      and os.path.normcase(got2) != os.path.normcase(fake_install),
      "得到 %s" % got2)

# 启动器组件用的是同一套逻辑
got3 = LauncherWidget._safe_cwd(exe)
check("启动器组件：同样不会把桌宠目录传下去",
      got3 is not None
      and os.path.normcase(got3) != os.path.normcase(fake_install),
      "得到 %s" % got3)

# ---- 2) 真起一个子进程，问它自己的 CWD ----
# cmd /c cd 会打印它的当前目录 —— 这是最直接的证据
cwd = RadialMenu._launch_cwd(exe)
p = subprocess.Popen([exe, "/c", "cd"], cwd=cwd,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     creationflags=0x08000000)
out = (p.communicate()[0] or b"").decode("gbk", "replace").strip()
check("子进程实际的工作目录不是桌宠目录（这才是会占住 DLL 的那条路）",
      os.path.normcase(fake_install) not in os.path.normcase(out),
      "子进程报告 CWD = %s" % out)

# ---- 3) 反面对照：不传 cwd 就会继承，证明这个坑真实存在 ----
p2 = subprocess.Popen([exe, "/c", "cd"],
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      creationflags=0x08000000)
out2 = (p2.communicate()[0] or b"").decode("gbk", "replace").strip()
check("反面对照：不传 cwd 时子进程确实继承了桌宠目录（坑是真的）",
      os.path.normcase(fake_install) in os.path.normcase(out2),
      "继承到 = %s" % out2)

# ---- 4) 代码层面：所有启动点都必须带 cwd ----
os.chdir(real_cwd)
src = open("pet_gravity.py", encoding="utf-8").read()
blk = src[src.index("    def _launch(self, path):"):src.index("    def _reshow_menu")]
n_popen = blk.count("subprocess.Popen(")
n_cwd = blk.count("cwd=cwd")
check("径向菜单 _launch 里每个 Popen 都带了 cwd（%d 个 Popen / %d 个 cwd=）"
      % (n_popen, n_cwd), n_popen > 0 and n_cwd >= n_popen)
check("os.startfile 也被包成了「临时切目录再切回」",
      "_startfile_outside" in src and "os.chdir(cwd)" in src)
wl = open("webchat_launcher.py", encoding="utf-8").read()
check("起浏览器时也传了 cwd（浏览器同样会占住 DLL）",
      "subprocess.Popen(args, creationflags=0x08000000,\n                         cwd=" in wl)

import shutil
shutil.rmtree(fake_install, ignore_errors=True)
print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
