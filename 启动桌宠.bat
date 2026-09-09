@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYW=C:\Users\well\AppData\Local\Python\bin\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ps = Get-CimInstance Win32_Process -Filter 'Name=''pythonw.exe'' or Name=''python.exe''' -ErrorAction SilentlyContinue; foreach ($p in $ps) { if ($p.CommandLine -like '*main.py*') { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } }"
start "" "%PYW%" main.py