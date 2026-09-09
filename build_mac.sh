#!/bin/bash
# oi桌宠 v0.2.0 macOS 构建脚本（必须在 Mac 上运行；PyInstaller 不支持跨平台打包）
#
# 前置准备（Mac 上执行一次）：
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
#   brew install python@3.11
#   /opt/homebrew/bin/python3.11 -m pip install PyQt5 pyyaml Pillow pyinstaller
#
set -e
cd "$(dirname "$0")"

PY=python3
if [ -x /opt/homebrew/bin/python3.11 ]; then
  PY=/opt/homebrew/bin/python3.11
fi

# 生成图标（mac 用 icns；没有则退回 png）
"$PY" _make_icon.py

echo "PyInstaller 打包（v0.2.0）..."
"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --name "oi桌宠" \
  --icon assets/oi.png \
  --add-data "assets:assets" \
  main.py

echo "复制运行目录（widgets/、config.yaml）到 .app 内..."
APP="dist/oi桌宠.app/Contents/MacOS"
mkdir -p "$APP/widgets"
cp -R widgets/* "$APP/widgets/" 2>/dev/null || true
if [ -f config.yaml ]; then cp config.yaml "$APP/"; fi

echo "打包完成：dist/oi桌宠.app  (v0.2.0)"
echo "注意：CPU/内存/电池/网络等系统状态模块仅支持 Windows，mac 上会显示'仅支持 Windows'。"
