#define MyAppName "oi桌宠"
#define MyAppVersion "0.8.2"

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
Source: "dist\oi桌宠.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "dist\webchat_sites.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "dist\widgets\*"; DestDir: "{app}\widgets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\oi桌宠.exe"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\oi桌宠.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\oi桌宠.exe"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
