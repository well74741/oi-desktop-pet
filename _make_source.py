# -*- coding: utf-8 -*-
"""导出开发源码包（白名单，不含用户数据/构建产物），供换机器继续开发。

用法：python _make_source.py        → dist/oi桌宠_v<版本>_源码.zip
"""

import fnmatch
import os
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))

# 白名单：只收这些（相对项目根目录的通配）
INCLUDE = [
    "*.py", "widgets/*.py", "assets/*",
    "README.md", "HANDOFF.md", "自定义模块开发指南.md", "画布方案.md",
    "requirements.txt", "config.yaml", "webchat_sites.json", ".gitignore",
    "oi_pet_v020.spec", "version_info.txt", "build.bat", "oi桌宠.iss",
    "build_mac.sh", "启动桌宠.bat", "启动桌宠.vbs",
]
# 兜底黑名单：用户数据（含 API Key）绝不能进包
EXCLUDE = [
    "pet_settings.json*", "chat_*.json", "*_data.json", "token_usage.json",
    "webchat_windows.json", "*.log", "*.zip", "*.exe",
]


def _version():
    import re
    txt = open(os.path.join(SRC, "module_core.py"), encoding="utf-8").read()
    return re.search(r'APP_VERSION\s*=\s*"([^"]+)"', txt).group(1)


def collect():
    files = []
    for root, dirs, names in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", "build", "build_out", "dist", ".git", ".claude",
                               "webchat_profile"}]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), SRC).replace("\\", "/")
            if not any(fnmatch.fnmatch(rel, pat) for pat in INCLUDE):
                continue
            if any(fnmatch.fnmatch(n, pat) for pat in EXCLUDE):
                continue
            files.append(rel)
    return sorted(files)


def main():
    ver = _version()
    os.makedirs(os.path.join(SRC, "dist"), exist_ok=True)
    dst = os.path.join(SRC, "dist", "oi桌宠_v%s_源码.zip" % ver)
    files = collect()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(SRC, rel), "oi桌宠/" + rel)
    print("source bundle: %s (%d files)" % (dst, len(files)))


if __name__ == "__main__":
    main()
