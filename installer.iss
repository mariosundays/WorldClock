; WorldClock installer (Inno Setup 6). Build the PyInstaller onedir first:
;   pyinstaller WorldClock.spec --clean -y
;   ISCC installer.iss            (or ISCC /DMyAppVersion=1.0.1 installer.iss)
; Per-user install, no admin. Settings in %APPDATA%\WorldClock are never touched.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName      "WorldClock"
#define MyAppPublisher "Mario Domingos"
#define MyAppURL       "https://github.com/mariosundays/WorldClock"
#define MyAppExeName   "WorldClock.exe"
#define DistDir        "dist\WorldClock"
#define RunKey         "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{6E0B7C1A-4D2F-4B8E-9A35-7C1F0D2E9B44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=installer_out
OutputBaseFilename=WorldClock-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
LicenseFile=LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start WorldClock when Windows starts"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; clear the previous build's bundled files so stale DLLs don't linger after an upgrade
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Same value the app's "Start with Windows" toggle writes, so the two stay in sync.
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"""; Tasks: startup
Root: HKCU; Subkey: "{#RunKey}"; ValueName: "{#MyAppName}"; Flags: deletevalue; Tasks: not startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start WorldClock now"; Flags: nowait postinstall

[Code]
procedure KillWorldClock();
var
  rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExeName}', '', SW_HIDE,
       ewWaitUntilTerminated, rc);
  Sleep(400);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillWorldClock();      // close a running copy before files are replaced
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillWorldClock();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // remove autostart however it was set (installer task or the tray toggle);
  // %APPDATA%\WorldClock (settings) is left alone on purpose
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', '{#MyAppName}');
end;
