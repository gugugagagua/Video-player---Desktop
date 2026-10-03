; ============================================================
;  视频集播放器 - Inno Setup 安装包脚本
;  编译：ISCC.exe installer.iss
;  依赖：先执行 build.bat 生成 dist\视频集播放器\
; ============================================================

#define MyAppName "视频集播放器"
#define MyAppVersion "1.7.1"
#define MyAppExeName "视频集播放器.exe"

[Setup]
AppId={{8F3A2B6C-1D4E-4A7B-9C2F-5E6D8A1B3C40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
; 产物文件名与 GitHub Release 的资产命名保持一致，发版时无需再手工改名
OutputBaseFilename=Video-Set-Player-{#MyAppVersion}
SetupIconFile=app_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=no
AllowNoIcons=yes
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

; ------------------------------------------------------------
;  中文界面消息（覆盖英文语言包中的关键文案）
; ------------------------------------------------------------
[Messages]
SetupWindowTitle=安装 - %1
WelcomeLabel1=欢迎使用 {#MyAppName} 安装向导
WelcomeLabel2=即将在您的电脑上安装 [name/ver]。%n%n建议关闭其他正在运行的程序后再继续。
SelectDirLabel3=安装程序将把 [name] 安装到以下文件夹。
SelectDirBrowseLabel=点击"下一步"继续；如需更换位置，请点击"浏览"。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁盘空间。
SelectTasksLabel2=请选择安装程序需要执行的附加任务，然后点击"下一步"继续。
SelectStartMenuFolderLabel3=安装程序将在开始菜单中创建程序快捷方式。
SelectStartMenuFolderBrowseLabel=点击"下一步"继续；如需更换位置，请点击"浏览"。
ReadyLabel1=安装程序已准备好开始安装 [name]。
ReadyLabel2a=点击"安装"开始安装；如需检查或修改设置，请点击"上一步"。
ReadyLabel2b=点击"安装"开始安装。
PreparingDesc=安装程序正在准备安装 [name]。
InstallingLabel=正在安装 [name]，请稍候……
FinishedHeadingLabel=安装完成
FinishedLabelNoIcons=[name] 已成功安装到您的电脑。
FinishedLabel=[name] 已成功安装到您的电脑，可双击快捷方式启动。
ClickFinish=点击"完成"结束安装程序。
RunEntryExec=运行 %1
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)...
ButtonCancel=取消
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成，确定要退出吗？%n%n如果现在退出，程序将不会被安装。
UninstallAppFullTitle=卸载 %1
ConfirmUninstall=确定要卸载 %1 吗？%n%n程序文件将被全部删除。
UninstalledAll=%1 已成功卸载。
UninstalledMost=%1 的卸载已完成。%n%n部分文件无法删除，可手动清理。
UninstalledAndNeedsRestart=要完成 %1 的卸载，必须重启电脑。%n%n现在重启吗？
UninstallStatusLabel=正在从您的电脑上删除 %1，请稍候……
