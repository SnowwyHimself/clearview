; Inno Setup script for the ClearView Windows installer.
;
; Configured to feel like a one-click Electron (electron-builder) installer:
; installs per-user (no admin prompt), skips the wizard pages (no folder
; questions), creates Start Menu + Desktop shortcuts, and launches the app.
;
; Packages the PyInstaller onedir output (dist\ClearView\). Built by the GitHub
; Actions Windows job.

#define MyAppName "ClearView"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{C1EA5B10-9E5D-4C2E-9C1F-A1B2C3D40001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Snowwy
; Per-user install (matches Electron one-click) — no UAC/admin prompt.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
; Skip every optional wizard page so it's basically one click.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
CloseApplications=yes
SetupIconFile=clearview.ico
UninstallDisplayIcon={app}\ClearView.exe
; Paths below are relative to THIS .iss file (packaging\), so step up to repo root.
OutputDir=..\dist
OutputBaseFilename=ClearView-{#MyAppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\ClearView\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\ClearView.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ClearView.exe"

[Run]
; Auto-launch after install (like Electron one-click installers do).
Filename: "{app}\ClearView.exe"; Flags: nowait postinstall skipifsilent
