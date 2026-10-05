# -*- mode: python ; coding: utf-8 -*-
"""oi桌宠 打包配置（Windows）。

一个开关（环境变量）：
  OI_ONEDIR=1     目录版：不打包成单文件，启动省去解包（0.4s vs 2.4s）

产物组合：单文件 = 便携发布；目录版 = 安装包内容（用户看不到目录结构）。

不再打包 QtWebEngine：它曾经只服务"聚合AI"内置网页窗，一家就占整包四分之三
（Qt5WebEngineCore.dll 97MB + icudtl.dat 10MB + ANGLE/Quick/Qml 一串），而内核
是 Chromium 83（2020），个别站点要靠伪装 UA 才能进。现在聚合AI 用系统浏览器的
`--app` 应用窗口（见 webchat_launcher.py）：网页端功能一个不少，内核随系统更新，
打包体积为零。
"""
import os

ONEDIR = os.environ.get("OI_ONEDIR", "") == "1"

block_cipher = None

# 项目实际只 import 了 QtCore / QtGui / QtWidgets / QtWinExtras
# （完整版再加 QtWebEngineWidgets），其余 Qt 绑定一律排除
_QT_UNUSED = [
    "QtWebEngineWidgets", "QtWebEngineCore", "QtWebEngine", "QtWebChannel",
    "QtQml", "QtQuick", "QtQuickWidgets", "QtQuick3D", "QtXmlPatterns",
    "QtMultimedia", "QtMultimediaWidgets", "QtBluetooth", "QtNfc",
    "QtSensors", "QtSerialPort", "QtSql", "QtTest", "QtHelp", "QtDesigner",
    "QtLocation", "QtPositioning", "QtWebSockets", "QtDBus", "QtOpenGL",
    "QtRemoteObjects", "QtTextToSpeech", "QtX11Extras", "QtMacExtras",
    "QtPrintSupport", "QtSvg",
]
excludes = [
    # Pillow 的打包钩子会顺带拉进 numpy（含 20MB OpenBLAS），项目完全没用到
    "numpy", "scipy", "pandas", "matplotlib",
    # 标准库/工具链里用不到的大件
    "tkinter", "unittest", "doctest", "pydoc_data", "lib2to3", "ensurepip",
    "distutils", "setuptools", "pip", "pytest", "pkg_resources",
] + ["PyQt5." + m for m in _QT_UNUSED]

def _dynamic_deps():
    """widgets/*.py 的依赖必须显式列出。

    组件是运行期按**文件路径**动态加载的（`spec_from_file_location`），
    PyInstaller 的静态分析看不到它们，它们 import 的东西一个都不会被打包。
    v0.9.2 就是这么坏的：`canvas.py`/`perler.py` 里有 `from widgets import icons`，
    而没有任何静态代码 import 过 widgets.icons，于是打包后画布/拼豆一律加载失败。
    这里扫描 widgets/*.py 的 import 语句自动补齐，新增组件不必再改 spec。
    """
    import ast
    import glob

    mods = set()
    wdir = "widgets"
    local = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(wdir, "*.py"))}
    for f in sorted(glob.glob(os.path.join(wdir, "*.py"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem != "__init__":
            mods.add("widgets." + stem)
        try:
            tree = ast.parse(open(f, encoding="utf-8").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods.add(node.module)
                if node.module == "widgets":
                    # from widgets import icons/kit -> 子模块要单独打包；
                    # from widgets import ModuleWidget 是类名，不是模块，跳过
                    for a in node.names:
                        if a.name in local:
                            mods.add("widgets." + a.name)
    return sorted(mods)


# 组件工具包与聚合AI 启动器/界面：运行期动态或延迟导入，需显式打包
# autostart 是函数里惰性 import 的，静态分析看不见，必须显式带上
hiddenimports = [
        'updater', 'update_ui', 'hotkey', 'lnk_target', 'app_focus',"widgets.kit", "webchat_launcher", "webchat_ui",
                 "webchat_dock",
                 "autostart"] + _dynamic_deps()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'),
           ('webchat_sites.json', '.'),
           ('README.md', '.'),
           ('自定义模块开发指南.md', '.')],   # 文档随 exe 打包（解压到临时目录）
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# excludes 只拦 Python 模块；Qt 的 DLL / 资源是钩子按目录整体收集的，得按文件名筛。
_DROP_ALWAYS = [
    "qt5/bin/opengl32sw.dll",   # 20MB 软件 OpenGL 兜底：纯 Widgets 界面不申请 GL
    "pil/_avif",                # 7.5MB AVIF 解码：桌宠图片只支持 png/jpg/gif/webp
]
_DROP_ALWAYS += [
    # QtWebEngine 全家 + 它依赖的 ANGLE / Quick / Qml（已不再内置浏览器内核）
    "webenginecore", "webenginewidgets", "webenginequick", "qtwebengineprocess",
    "qtwebengine_", "icudtl.dat", "qt5quick", "qt5qml", "qtquick", "qml/",
    "qt5webchannel", "qt5positioning", "d3dcompiler_47.dll", "libglesv2.dll",
    "libegl.dll",
]
_drop = list(_DROP_ALWAYS)


def _strip(entries):
    """按文件名子串剔除不需要打包的二进制/资源，并统计省下多少。"""
    kept, saved = [], 0
    for e in entries:
        key = str(e[0]).replace("\\", "/").lower()
        if any(pat in key for pat in _drop):
            try:
                saved += os.path.getsize(e[1])
            except Exception:
                pass
            continue
        kept.append(e)
    if saved:
        print("[oi] 剔除 %d 项，源文件省 %.1f MB" % (len(entries) - len(kept),
                                                    saved / 1048576.0))
    return kept


a.binaries = _strip(a.binaries)
a.datas = _strip(a.datas)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_exe_args = [pyz, a.scripts]
_exe_kw = dict(
    name='oi桌宠',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,               # 无控制台窗口
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
    version='version_info.txt',  # 由 build.bat 依 APP_VERSION 生成
)

if ONEDIR:
    # 目录版：二进制留在 COLLECT 里，启动不必把整包解到 %TEMP%
    exe = EXE(*_exe_args, [], exclude_binaries=True, **_exe_kw)
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
                   strip=False, upx=True, upx_exclude=[], name='oi桌宠_app')
else:
    exe = EXE(*_exe_args, a.binaries, a.zipfiles, a.datas, [],
              runtime_tmpdir=None, **_exe_kw)
