; Inno Setup script for YDM. build.bat compiles it after PyInstaller:
;   ISCC.exe /DAppVersion=1.0.0 installer\YDM.iss   ->  dist\YDM-Setup-1.0.0.exe
; Installs per user (no admin prompt) unless the user picks "all users".

#define AppName "YDM"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F3C1B52-8E4D-4A57-9C1E-7D2B5A9E0F14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Yazeed
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=YDM-Setup-{#AppVersion}
SetupIconFile=..\assets\ydm.ico
UninstallDisplayIcon={app}\YDM.exe
UninstallDisplayName={#AppName} Download Manager
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start YDM with &Windows (minimized to tray)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\YDM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\YDM.exe"; AppUserModelID: "Yazeed.YDM"
Name: "{autoprograms}\{#AppName} browser extension folder"; Filename: "{app}\extension"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\YDM.exe"; AppUserModelID: "Yazeed.YDM"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
    ValueData: """{app}\YDM.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\YDM.exe"; Description: "Launch YDM now"; Flags: nowait postinstall skipifsilent
; In-app updates run this installer with /VERYSILENT /RELAUNCH: start YDM again afterwards.
Filename: "{app}\YDM.exe"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), '/RELAUNCH') = 0 then
      Result := True;
end;

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im YDM.exe"; Flags: runhidden; RunOnceId: "KillYDM"
