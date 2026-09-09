@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== oi桌宠 v0.8.2 packaging script (Python 3.12 environment) =====
set "PYENV=C:\Users\well\AppData\Local\oi-packenv\Scripts\python.exe"
set "ISCC=C:\Program Files\Inno Setup 7\ISCC.exe"
if not exist "%PYENV%" (
    echo [ERROR] Missing packaging environment: %PYENV%
    exit /b 1
)

echo [1/3] Building PyInstaller executable...
"%PYENV%" -m PyInstaller --noconfirm --clean oi_pet_v020.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo [2/3] Copying external runtime data...
copy /Y config.yaml dist\config.yaml >nul
copy /Y webchat_sites.json dist\webchat_sites.json >nul
if exist dist\widgets rmdir /S /Q dist\widgets
xcopy /E /I /Y widgets dist\widgets >nul
if exist dist\widgets\__pycache__ rmdir /S /Q dist\widgets\__pycache__
copy /Y dist\oi桌宠.exe dist\oi桌宠0.8.2.exe >nul

echo [3/3] Building installer...
if not exist "%ISCC%" (
    echo [WARN] Inno Setup not found; skipped installer.
    exit /b 0
)
"%ISCC%" oi桌宠_v0.8.2.iss
if errorlevel 1 (
    echo Installer build failed.
    exit /b 1
)

echo Done: dist\oi桌宠0.8.2.exe + dist\oi桌宠_Setup_v0.8.2.exe
