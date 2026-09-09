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

if getattr(sys, "frozen", False):
    # 打包后数据文件与 exe 同目录（widgets/ 也在 exe 旁），避免 _MEIPASS 只读
    DATA_DIR = os.path.dirname(sys.executable)
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))

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
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
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
