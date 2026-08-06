; Inno Setup script for the ClearView Windows installer.
; Packages the PyInstaller onedir output (dist\ClearView\) into a normal
; Windows installer with Start Menu + optional Desktop shortcuts and an
; uninstaller. Built by the GitHub Actions Windows job.

#define MyAppName "ClearView"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{C1EA5B10-9E5D-4C2E-9C1F-A1B2C3D40001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Snowwy
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Paths below are relative to THIS .iss file (packaging\), so step up to repo root.
OutputDir=..\dist
OutputBaseFilename=ClearView-{#MyAppVersion}-Windows-x64-Setup
SetupIconFile=clearview.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\ClearView\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\ClearView.exe"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ClearView.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClearView.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
