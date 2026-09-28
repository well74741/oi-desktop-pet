# -*- coding: utf-8 -*-
"""统一数据层：JSON 持久化的安全读写。

集中管理项目根目录下的用户数据文件（todo_data.json / counter_data.json /
chat_history.json / chat_summary.json 等），提供：
- 统一路径解析（data_path）
- 带锁的读 / 原子写（先写临时文件再替换，断电/崩溃不产生半截文件）
- 类型校验（read_list / read_dict 保证返回正确类型，损坏文件不拖垮程序）

pet_settings.json 走 pet_gravity.PetAPI（有独立的设置合并逻辑），不在此层。
"""
import json
import os
import sys
import tempfile
import threading


def make_temp_file(d, prefix=".oi_", suffix=".tmp"):
    """在目录 d 里独占创建一个临时文件，返回 (fd, path)。

    **不要换回 tempfile.mkstemp**：Windows 上它遇到"ACL 不可写"的目录会死循环。
    它的重试判据是 `os.access(dir, W_OK)`，而 os.access 在 Windows 只看文件系统的
    只读属性、根本不看 ACL，于是对 Program Files 这类目录一直返回 True，
    mkstemp 就一直 continue —— 而 Windows 上 tempfile.TMP_MAX 是 21 亿。
    结果：装在 Program Files 的版本只要尝试保存任何数据，就 100% 占满一个 CPU 核
    永久卡死（v0.9.2 的"点桌宠卡死"就是这个）。
    这里只试有限次，权限类错误立刻上抛。
    """
    import binascii
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    last = None
    for _ in range(8):
        path = os.path.join(
            d, prefix + binascii.hexlify(os.urandom(6)).decode("ascii") + suffix)
        try:
            return os.open(path, flags, 0o600), path
        except FileExistsError as e:      # 重名才值得重试
            last = e
    raise last or OSError("无法在 %s 创建临时文件" % d)


def _is_writable(d):
    """目录能不能写（真的建个文件试，ACL 拒写也能测出来）。"""
    probe = None
    try:
        if not os.path.isdir(d):
            return False
        fd, probe = make_temp_file(d, prefix=".oi_w_")
        os.close(fd)
        return True
    except Exception:
        return False
    finally:
        if probe:
            try:
                os.remove(probe)
            except Exception:
                pass


def _migrate_json(src, dst):
    """把 exe 旁边残留的用户数据搬到新目录（只搬一次，已存在的不覆盖）。"""
    try:
        import glob
        import shutil
        for f in glob.glob(os.path.join(src, "*.json")) + \
                glob.glob(os.path.join(src, "*.json.bak")):
            t = os.path.join(dst, os.path.basename(f))
            if not os.path.exists(t):
                shutil.copy2(f, t)
    except Exception:
        pass


def _pick_data_dir():
    """用户数据目录。

    源码模式：项目根目录。
    打包后：优先 exe 旁边——便携版这样"一个文件夹拷走"最省事；但安装版装在
    `C:/Program Files/...`，那里**普通用户不可写**，此前设置/待办/聊天记录
    一律存不下来，而且异常被各处的 except 吞掉，连个提示都没有（v0.9.2 的
    "点确定不保存"就有这一层原因）。所以不可写时退到 %LOCALAPPDATA%/oi桌宠，
    并把 exe 旁边可能残留的数据搬过去。
    """
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    beside = os.path.dirname(sys.executable)
    if _is_writable(beside):
        return beside
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    if base:
        d = os.path.join(base, "oi桌宠")
        try:
            os.makedirs(d, exist_ok=True)
            if _is_writable(d):
                _migrate_json(beside, d)
                return d
        except Exception:
            pass
    return beside


DATA_DIR = _pick_data_dir()

_LOCK = threading.RLock()


def data_path(name):
    """数据文件绝对路径（项目根目录）。"""
    return os.path.join(DATA_DIR, name)


def read_json_path(path, default=None):
    """按绝对路径读 JSON；不存在或损坏时返回 default（不抛异常）。"""
    with _LOCK:
        try:
            if not os.path.exists(path):
                return default
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default


def write_json_path(path, obj):
    """按绝对路径原子写 JSON（临时文件 + os.replace），带锁。"""
    with _LOCK:
        tmp = None
        try:
            d = os.path.dirname(path) or "."
            fd, tmp = make_temp_file(d, prefix=".oi_json_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except Exception:
                    pass


def read_json(name, default=None):
    """读 JSON 文件（按名称，位于 DATA_DIR）；不存在或损坏时返回 default。"""
    return read_json_path(data_path(name), default)


def write_json(name, obj):
    """原子写 JSON（按名称，位于 DATA_DIR），带锁。"""
    write_json_path(data_path(name), obj)


def read_list(name):
    v = read_json(name, [])
    return v if isinstance(v, list) else []


def read_dict(name):
    v = read_json(name, {})
    return v if isinstance(v, dict) else {}


def mutate_json(name, default, fn):
    """fn(obj) -> 原地修改 obj，返回 None 表示不变（不写盘）；否则写盘并返回 obj。"""
    with _LOCK:
        obj = read_json(name, default)
        if fn(obj) is None:
            return None
        write_json(name, obj)
        return obj
