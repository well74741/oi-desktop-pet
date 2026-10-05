@echo off
chcp 65001 >nul
cd /d "%~dp0"
title oi桌宠 打包

REM ===== 双击即可打包 =====
REM 实际干活的是 build.bat（先跑全部测试，全过才出包）。这里只负责：
REM   - 用中文告诉你在干什么、结果在哪；
REM   - 结束后停住窗口（双击运行时，不停住窗口一闪就没了，看不到成功还是失败）；
REM   - 成功后打开 dist 文件夹，并把安装包的 sha256 打出来。

echo.
echo ==================================================
echo   oi桌宠 打包
echo   第一步会跑全部自动化测试，全过才会出包（约 3~5 分钟）
echo ==================================================
echo.

call "%~dp0build.bat"
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo ==================================================
    echo   [失败] 打包没有完成（错误码 %RC%）
    echo   往上翻看第一条 [ERROR] 或 FAIL；测试没过时不会出包。
    echo ==================================================
    echo.
    pause
    exit /b %RC%
)

REM 读版本号：命令里只能有一对引号 —— for /f 遇到多对引号会吃掉首尾两个，路径就断了
if not defined PYENV set "PYENV=%LOCALAPPDATA%\oi-packenv\Scripts\python.exe"
for /f %%v in ('"%PYENV%" _make_version.py') do set "VER=%%v"
set "SETUP=dist\oi-pet_Setup_v%VER%.exe"

echo ==================================================
echo   [成功] v%VER% 打包完成
echo.
if exist "%SETUP%" (
    echo   安装包：%SETUP%
    echo   上传到 GitHub Release 时文件名不用改。
    echo.
    echo   sha256（GitHub 上附件的 digest 应当和它一致）：
    certutil -hashfile "%SETUP%" SHA256 | findstr /r /x "[0-9a-f]*"
) else (
    echo   [注意] 没找到安装包：没装 Inno Setup 时只会生成便携版。
)
echo ==================================================
echo.
start "" explorer "%~dp0dist"
pause
