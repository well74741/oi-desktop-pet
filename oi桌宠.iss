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
; 安装前先让旧版退出，否则替换 _internal 里的 DLL 会撞上
; "DeleteFile failed; code 5 拒绝访问"（用户实测 VCRUNTIME140.dll）。
; 三道保险，从温和到强硬：
;  1) AppMutex —— 认的是具名互斥体（main.py 的 _create_app_mutex 建的那个）。
;     原来单实例只有 QLockFile 文件锁，安装程序看不见，所以以前连提示都没有。
;  2) CloseApplications=yes —— 用 Restart Manager 找出占用文件的进程并关掉。
;  3) [Code] 里的 taskkill 兜底 —— 托盘进程有时 Restart Manager 抓不到。
AppMutex=oi_pet_desktop_single_instance
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

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

[Code]
procedure KillOldInstance;
var
  code: Integer;
begin
  { 只结束我们自己的 exe。**绝不碰 msedge** —— 聚合AI 用的是用户自己的浏览器，
    杀掉会连带关掉他正在用的网页。浏览器进程持有的是 LOCALAPPDATA 下的
    webchat_profile，不在安装目录里，本来也不会挡安装。 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "oi桌宠.exe"', '',
       SW_HIDE, ewWaitUntilTerminated, code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillOldInstance;
  Sleep(800);          { 给文件句柄一点时间真正释放 }
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillOldInstance;     { 卸载同理：不先退出就删不干净 }
  Sleep(500);
  Result := True;
end;
