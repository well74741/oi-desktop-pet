; oi桌宠 安装包脚本（版本号由 build.bat 通过 /DMyAppVersion 传入，不写死）
#define MyAppName "oi桌宠"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8D2A65D1-83F4-4EA4-9A3E-2A2F2D60D707}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\oi桌宠.exe
OutputDir=dist
OutputBaseFilename=oi桌宠_Setup_v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=no

[Files]
; 目录版（onedir）：启动不必把整包解压到 %TEMP%，实测 0.5s vs 单文件 2.7s
Source: "build_out\oi桌宠_app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "webchat_sites.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "widgets\*"; DestDir: "{app}\widgets"; Excludes: "__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\oi桌宠.exe"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\oi桌宠.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\oi桌宠.exe"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
