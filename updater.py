# -*- coding: utf-8 -*-
"""在线更新：查 GitHub Release → 下载安装包 → 校验 → 交给安装程序。

只用标准库，不碰 Qt（界面在 update_ui.py），所以可以单独拿假服务器测。

几个踩过、查过的点（都有出处，别凭印象改）：
- **GitHub 会给附件改名**：官方文档原话 "GitHub renames asset filenames that have
  special characters, non-alphanumeric characters…"。实测 `oi桌宠_Setup_v0.9.38.exe`
  上传后变成 `oi._Setup_v0.9.38.exe`。所以挑附件只认"名字带 setup、以 .exe 结尾"
  这两个特征，不认完整文件名。
- **校验值**：附件对象里有 `digest`（形如 `sha256:…`），但文档说它**可能为空**。
  有就必须对上，对不上就删掉不装；没有就只核对大小。
- **安装程序要管理员权限**：必须用 ShellExecute（`os.startfile`）启动才会弹 UAC，
  用 subprocess 直接 CreateProcess 会报 740（需要提升权限）。
- **工作目录别给安装目录**：v0.9.31 的教训 —— 子进程继承 CWD，会占住
  `_internal\\` 里的 DLL。这里把 CWD 设成下载目录。
- **静默安装参数**：`/SILENT` 只显示安装进度窗；**必须带 `/NOCLOSEAPPLICATIONS`** ——
  Inno 文档原话：CloseApplications=yes 且静默运行时 "Setup will always close and
  restart such applications"，也就是会**不打招呼地关掉**占用文件的其他程序（比如
  当年占着 VCRUNTIME140.dll 的 Photoshop，没保存的工作就没了）。旧版桌宠照样会被
  关掉：那是 .iss 里 PrepareToInstall 的 taskkill 做的，只针对我们自己的 exe。
  故意**不加** `/SUPPRESSMSGBOXES`：万一有文件被占用，要让用户看到提示自己处理。
"""
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = "well74741/oi-desktop-pet"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
PAGE_LATEST = "https://github.com/%s/releases/latest" % REPO
INSTALL_ARGS = "/SILENT /NOCLOSEAPPLICATIONS /NORESTART"
_UA = "oi-desktop-pet-updater"
_CHUNK = 64 * 1024


class UpdateError(Exception):
    """给用户看的错误：文案就是 str(e)，直接显示。"""


class UpdateCancelled(UpdateError):
    pass


# ---------- 版本号 ----------
def parse_version(s):
    """'v0.9.37' / '0.9.37' / 'oi桌宠 0.9.37' → (0, 9, 37)；认不出返回 None。"""
    m = re.search(r"(\d+(?:\.\d+)+)", str(s or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(remote, local):
    r, l = parse_version(remote), parse_version(local)
    if not r or not l:
        return False
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))


# ---------- 运行形态 ----------
def install_mode():
    """'installed' / 'portable' / 'source'。

    只有安装版能自动更新：源码运行该用 git pull；便携版装了安装包也不会替换它本身。
    安装版的判断依据：exe 旁边有 Inno Setup 的卸载程序 unins000.exe。
    """
    if not getattr(sys, "frozen", False):
        return "source"
    d = os.path.dirname(os.path.abspath(sys.executable))
    try:
        if any(n.lower().startswith("unins") and n.lower().endswith(".exe")
               for n in os.listdir(d)):
            return "installed"
    except OSError:
        pass
    return "portable"


# ---------- 查询 ----------
def pick_asset(assets):
    """挑安装包附件：名字含 setup 且以 .exe 结尾（不认完整文件名，见模块说明）。"""
    for a in assets or []:
        n = str(a.get("name") or "").lower()
        if (n.endswith(".exe") and "setup" in n
                and str(a.get("state") or "uploaded") == "uploaded"):
            return a
    return None


def _sha_of(asset):
    d = str(asset.get("digest") or "")
    if d.lower().startswith("sha256:"):
        h = d.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", h):
            return h
    return None


def fetch_latest(api=API_LATEST, timeout=8.0):
    """查最新发布，返回 dict；出错抛 UpdateError（文案可直接给用户看）。"""
    req = urllib.request.Request(api, headers={
        "User-Agent": _UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(2 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("还没有发布任何版本")
        if e.code in (403, 429):
            raise UpdateError("检查得太频繁了（GitHub 每小时最多 60 次），请稍后再试")
        raise UpdateError("GitHub 返回错误（HTTP %d）" % e.code)
    except (urllib.error.URLError, socket.timeout, OSError):
        raise UpdateError("连不上 GitHub，请检查网络")
    except ValueError:
        raise UpdateError("GitHub 返回的内容无法解析")
    tag = str(data.get("tag_name") or "")
    ver = parse_version(tag)
    if not ver:
        raise UpdateError("最新发布的标签不是版本号：%s" % (tag or "（空）"))
    a = pick_asset(data.get("assets"))
    return {
        "version": ".".join(str(x) for x in ver),
        "tag": tag,
        "title": str(data.get("name") or ""),
        "notes": str(data.get("body") or ""),
        "page": str(data.get("html_url") or PAGE_LATEST),
        "asset": None if a is None else {
            "name": str(a.get("name") or ""),
            "url": str(a.get("browser_download_url") or ""),
            "size": int(a.get("size") or 0),
            "sha256": _sha_of(a),
        },
    }


# ---------- 下载 ----------
def download_dir():
    d = os.path.join(tempfile.gettempdir(), "oi-pet-update")
    os.makedirs(d, exist_ok=True)
    return d


def clean_downloads(keep=None):
    """清掉下载目录里的旧安装包（每次下载前调，别让临时目录越攒越多）。"""
    try:
        d = download_dir()
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if keep and os.path.abspath(p) == os.path.abspath(keep):
                continue
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def download(asset, progress=None, cancel=None, timeout=20.0, dest_dir=None):
    """下载到临时目录，边下边算 sha256，返回 (路径, 是否做了 sha256 校验)。

    progress(已下载字节, 总字节, 字节/秒)；cancel 是 threading.Event，置位即取消。
    任何失败都会删掉半截文件，绝不留下一个"看起来像安装包"的残件。
    """
    url = str(asset.get("url") or "")
    if not url:
        raise UpdateError("这个版本没有可下载的安装包")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(asset.get("name") or "")) or "setup.exe"
    dest = os.path.join(dest_dir or download_dir(), name)
    part = dest + ".part"
    expected = int(asset.get("size") or 0)
    sha = asset.get("sha256")
    h = hashlib.sha256()
    done = 0
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            total = expected or int(r.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled("已取消下载")
                b = r.read(_CHUNK)
                if not b:
                    break
                f.write(b)
                h.update(b)
                done += len(b)
                if progress is not None:
                    progress(done, total, done / max(0.001, time.monotonic() - t0))
    except UpdateError:
        _rm(part)
        raise
    except urllib.error.HTTPError as e:
        _rm(part)
        raise UpdateError("下载失败（HTTP %d）" % e.code)
    except (urllib.error.URLError, socket.timeout, OSError):
        _rm(part)
        raise UpdateError("下载中断，请检查网络后重试")
    if expected and done != expected:
        _rm(part)
        raise UpdateError("下载不完整（%d / %d 字节），请重试" % (done, expected))
    if sha and h.hexdigest().lower() != sha:
        _rm(part)
        raise UpdateError("校验失败：下载到的文件和发布的不一致，已删除，没有安装")
    os.replace(part, dest)
    return dest, bool(sha)


# ---------- 安装 ----------
def launch_installer(path):
    """启动安装程序（会弹管理员授权）。用户在 UAC 上点"否"时抛 UpdateError。"""
    if not os.path.isfile(path):
        raise UpdateError("安装包不见了，请重新下载")
    try:
        # ShellExecute 才会触发 UAC；CWD 给下载目录（见模块说明）
        os.startfile(path, "open", INSTALL_ARGS, os.path.dirname(path))
    except OSError as e:
        if getattr(e, "winerror", None) == 1223:      # ERROR_CANCELLED
            raise UpdateError("你取消了管理员授权，没有安装")
        raise UpdateError("启动安装程序失败：%s" % e)
