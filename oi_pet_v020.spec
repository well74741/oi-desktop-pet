# -*- mode: python ; coding: utf-8 -*-
# oi桌宠 v0.8.0 打包配置（Windows 单文件）
block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'),
           ('webchat_sites.json', '.'),
           ('README.md', '.'),
           ('自定义模块开发指南.md', '.'),
           ('HANDOFF.md', '.')],   # 文档随 exe 打包（解压到临时目录）
    # 组件工具包与聚合AI 面板：运行期动态/延迟导入，需显式打包
    # QtWebEngineWidgets 让 PyInstaller 收集 QtWebEngineProcess 等运行依赖
    hiddenimports=['widgets.kit', 'webchat_panel',
                   'PyQt5.QtWebEngineWidgets', 'PyQt5.QtWebEngineCore'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='oi桌宠',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # 无控制台窗口
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
    version='version_info.txt',  # exe 文件版本 v0.7.0
)
