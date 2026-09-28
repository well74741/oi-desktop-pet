# -*- coding: utf-8 -*-
"""依 module_core.APP_VERSION 生成 version_info.txt，并把版本号打到标准输出。

版本号只维护 module_core.APP_VERSION 一处；exe 文件属性、安装包版本、产物文件名
都由 build.bat 调用本脚本后统一取用，避免发版时漏改某个文件。
"""

import io
import os
import re
import sys

SRC = os.path.dirname(os.path.abspath(__file__))

_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(a)d, %(b)d, %(c)d, 0),
    prodvers=(%(a)d, %(b)d, %(c)d, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'oi桌宠'),
          StringStruct('FileDescription', 'oi桌宠桌面宠物 v%(v)s'),
          StringStruct('FileVersion', '%(v)s'),
          StringStruct('InternalName', 'oi桌宠'),
          StringStruct('OriginalFilename', 'oi桌宠.exe'),
          StringStruct('ProductName', 'oi桌宠'),
          StringStruct('ProductVersion', '%(v)s')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_version():
    txt = io.open(os.path.join(SRC, "module_core.py"), encoding="utf-8").read()
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', txt, re.M)
    if not m:
        raise SystemExit("module_core.py 里找不到 APP_VERSION")
    return m.group(1)


def main():
    ver = app_version()
    nums = (re.findall(r"\d+", ver) + ["0", "0", "0"])[:3]
    io.open(os.path.join(SRC, "version_info.txt"), "w", encoding="utf-8").write(
        _TEMPLATE % {"v": ver, "a": int(nums[0]), "b": int(nums[1]), "c": int(nums[2])})
    sys.stdout.write(ver)


if __name__ == "__main__":
    main()
