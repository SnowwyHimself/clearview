; Inno Setup script for the ClearView Windows installer.
; Packages the PyInstaller onedir output (dist\ClearView\) into a normal
; Windows installer with Start Menu + optional Desktop shortcuts and an
; uninstaller. Built by the GitHub Actions Windows job.

#define MyAppName "ClearView"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{C1EA5B10-9E5D-4C2E-9C1F-CLEARVIEW0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Snowwy
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ClearView-{#MyAppVersion}-Windows-x64-Setup
SetupIconFile=packaging\clearview.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\ClearView\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\ClearView.exe"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ClearView.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClearView.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
