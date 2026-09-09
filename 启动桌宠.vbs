Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "C:\Users\well\AppData\Local\Python\bin\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
psCmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command ""$ps = Get-CimInstance Win32_Process -Filter 'Name=''pythonw.exe'' or Name=''python.exe''' -ErrorAction SilentlyContinue; foreach ($p in $ps) { if ($p.CommandLine -like '*main.py*') { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } }"""
WshShell.Run psCmd, 0, True
WshShell.Run """" & pyw & """ main.py", 0, False