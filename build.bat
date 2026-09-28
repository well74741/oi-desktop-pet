@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

REM ===== oi桌宠 打包脚本（版本号取自 module_core.APP_VERSION，不写死） =====
REM   build.bat            产出便携单文件 + 安装包
REM 打包 venv 默认在 %LOCALAPPDATA%\oi-packenv；在别处时先 set PYENV=...\python.exe

if not defined PYENV set "PYENV=%LOCALAPPDATA%\oi-packenv\Scripts\python.exe"
if not defined ISCC set "ISCC=C:\Program Files\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%PYENV%" (
    echo [ERROR] Missing packaging environment: %PYENV%
    echo         py -3.12 -m venv "%%LOCALAPPDATA%%\oi-packenv"
    exit /b 1
)

echo [1/5] Reading version and writing version_info.txt...
for /f %%v in ('"%PYENV%" _make_version.py') do set "VER=%%v"
if not defined VER (
    echo Failed to read APP_VERSION.
    exit /b 1
)
echo         version = %VER%

echo [2/5] Building portable single-file exe...
set "OI_ONEDIR="
"%PYENV%" -m PyInstaller --noconfirm --clean --distpath build_out --workpath build oi_pet_v020.spec
if errorlevel 1 exit /b 1
if not exist dist mkdir dist
move /Y "build_out\oi桌宠.exe" "dist\oi桌宠%VER%.exe" >nul

echo [3/5] Building folder build for the installer...
set "OI_ONEDIR=1"
"%PYENV%" -m PyInstaller --noconfirm --clean --distpath build_out --workpath build oi_pet_v020.spec
if errorlevel 1 exit /b 1
set "OI_ONEDIR="

echo [4/5] Refreshing runtime data next to the portable exe...
copy /Y config.yaml dist\config.yaml >nul
copy /Y webchat_sites.json dist\webchat_sites.json >nul
if exist dist\widgets rmdir /S /Q dist\widgets
xcopy /E /I /Y widgets dist\widgets >nul
if exist dist\widgets\__pycache__ rmdir /S /Q dist\widgets\__pycache__

echo [5/5] Building installer...
if not exist "%ISCC%" (
    echo [WARN] Inno Setup not found; skipped installer.
    goto done
)
"%ISCC%" /DMyAppVersion=%VER% "oi桌宠.iss"
if errorlevel 1 exit /b 1

:done
echo.
echo Done:
echo   dist\oi桌宠%VER%.exe          portable single file
echo   dist\oi桌宠_Setup_v%VER%.exe        installer (folder build, faster start)
endlocal
