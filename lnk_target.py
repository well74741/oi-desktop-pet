# -*- coding: utf-8 -*-
"""直接读 .lnk 文件取出目标路径（纯 Python，不起进程）。

为什么要有它：原来每解析一个快捷方式都要起一次 PowerShell 调 WScript.Shell，
实测**每个 0.52 秒**（几乎全是起进程的开销）；菜单里放几个 .lnk，每次启动就是
几秒的后台开销。.lnk 是微软公开的二进制格式（[MS-SHLLINK]），目标路径就写在
文件里，直接读出来是毫秒级。

只处理最常见的两种存法，读不出来就返回 ""，由调用方退回 PowerShell（行为不变）：
- LinkInfo 里的本地路径（LocalBasePath + CommonPathSuffix，有 Unicode 版优先用）；
- EnvironmentVariableDataBlock 里带环境变量的路径（如 %ProgramFiles%\\…，展开后用）。
只有 IDList、没有 LinkInfo 的快捷方式（应用商店应用、部分系统项）不在此列。

结构对照 [MS-SHLLINK] 2.1 ShellLinkHeader / 2.3 LinkInfo / 2.5 ExtraData。
"""
import os
import struct

_HEADER_SIZE = 0x4C
_HAS_ID_LIST = 0x01
_HAS_LINK_INFO = 0x02
_HAS_EXP_STRING = 0x200
_VOLUME_AND_LOCAL_PATH = 0x01
_ENV_BLOCK_SIG = 0xA0000001
_MAX_LNK_BYTES = 1 << 20          # 正常的 .lnk 都只有几 KB；过大的不是正常快捷方式


def _cstr(buf, off, wide):
    """从 off 起读一个以 NUL 结尾的字符串。"""
    if off <= 0 or off >= len(buf):
        return ""
    if wide:
        end = off
        while end + 1 < len(buf) and buf[end:end + 2] != b"\x00\x00":
            end += 2
        return buf[off:end].decode("utf-16-le", "replace")
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    return buf[off:end].decode("mbcs", "replace")


def _from_link_info(data, pos):
    """解析 LinkInfo，返回 (本地完整路径, LinkInfo 总长度)。"""
    if pos + 28 > len(data):
        return "", 0
    size, hsize, flags, _vol, base_off, _net, suffix_off = struct.unpack_from(
        "<7I", data, pos)
    li = data[pos:pos + size]
    if not flags & _VOLUME_AND_LOCAL_PATH:
        return "", size              # 网络路径等：交给 PowerShell
    if hsize >= 0x24 and len(li) >= 0x24:
        ubase, usuffix = struct.unpack_from("<2I", li, 28)
        base = _cstr(li, ubase, True)
        suffix = _cstr(li, usuffix, True)
    else:
        base = _cstr(li, base_off, False)
        suffix = _cstr(li, suffix_off, False)
    if not base:
        return "", size
    if suffix:
        base = base.rstrip("\\") + "\\" + suffix
    return base, size


def _skip_string_data(data, pos, flags):
    """跳过 StringData（名称/相对路径/工作目录/参数/图标，按 flags 依次出现）。"""
    wide = bool(flags & 0x80)                    # IsUnicode
    for bit in (0x04, 0x08, 0x10, 0x20, 0x40):
        if flags & bit:
            if pos + 2 > len(data):
                return len(data)
            n = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + n * (2 if wide else 1)
    return pos


def _from_env_block(data, pos):
    """在 ExtraData 里找 EnvironmentVariableDataBlock，返回展开后的路径。"""
    while pos + 8 <= len(data):
        bsize, sig = struct.unpack_from("<2I", data, pos)
        if bsize < 4:
            break                               # TerminalBlock
        if sig == _ENV_BLOCK_SIG and bsize >= 8 + 260 + 520:
            raw = data[pos + 8 + 260:pos + 8 + 260 + 520]
            s = raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
            if not s:
                s = data[pos + 8:pos + 8 + 260].split(b"\x00", 1)[0].decode(
                    "mbcs", "replace")
            return os.path.expandvars(s)
        pos += bsize
    return ""


def target(path):
    """返回 .lnk 指向的路径；读不出来（或不是 .lnk）返回 ""，绝不抛异常。"""
    try:
        if os.path.getsize(path) > _MAX_LNK_BYTES:
            return ""
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < _HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != _HEADER_SIZE:
            return ""
        flags = struct.unpack_from("<I", data, 20)[0]
        pos = _HEADER_SIZE
        if flags & _HAS_ID_LIST:
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        local = ""
        if flags & _HAS_LINK_INFO:
            local, size = _from_link_info(data, pos)
            pos += size
        # 带环境变量的目标（%ProgramFiles%\…）以展开后的为准：LinkInfo 里存的是
        # 创建快捷方式那台机器上的路径，换了机器/盘符可能就不对了
        if flags & _HAS_EXP_STRING:
            env = _from_env_block(data, _skip_string_data(data, pos, flags))
            if env:
                return env
        return local
    except Exception:
        return ""
