; Inno Setup script - BellDesktopSetup.exe (the desktop edition of IAJ Bell: the system and the ringing in one program)
; Build (after PyInstaller):  iscc /DMyAppVersion=2026.11.23 winapp\BellDesktop.iss
; - installs for the current user only (no administrator rights needed) in %LOCALAPPDATA%\Programs
; - starts with Windows (optional task) so the bell rings after the computer is switched on and the user signs in
; - the data (%LOCALAPPDATA%\IAJ Bell) is never touched by an update or by uninstalling
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "IAJ Bell"
#define MyAppPublisher "AcsVe"
#define MyAppExe "BellDesktop.exe"
#define MyToolsExe "BellDesktopTools.exe"

[Setup]
AppId={{B7E2C4A1-6D35-4F8B-9A27-5C1E0D3F8B64}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=BellDesktopSetup
SetupIconFile=..\bell_player\bell.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
arabic.OpenAdmin=جرس الحصص (لوحة الإدارة)
english.OpenAdmin=IAJ Bell (admin panel)
arabic.LicensePage=الترخيص وبصمة الكمبيوتر
english.LicensePage=License and computer fingerprint
arabic.StopBell=إيقاف الجرس
english.StopBell=Stop the bell
arabic.AutoStart=تشغيل الجرس تلقائياً مع ويندوز (موصى به)
english.AutoStart=Start the bell automatically with Windows (recommended)

[Tasks]
Name: "autostart"; Description: "{cm:AutoStart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\BellDesktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{cm:OpenAdmin}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:LicensePage}"; Filename: "{app}\{#MyAppExe}"; Parameters: "--page admin/license"
Name: "{group}\{cm:StopBell}"; Filename: "{app}\{#MyToolsExe}"; Parameters: "--stop"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Parameters: "--background"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyToolsExe}"; Parameters: "--stop"; Flags: runhidden; RunOnceId: "StopBell"
