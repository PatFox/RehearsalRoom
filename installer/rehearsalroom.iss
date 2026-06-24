; Rehearsal Room — Inno Setup installer script
; Build with: iscc installer\rehearsalroom.iss
; Requires Inno Setup 6: https://jrsoftware.org/isdl.php

#define AppName      "Rehearsal Room"
; AppVersion is passed from build.bat via /DAppVersion=x.y.z
; This default is used when building manually with iscc directly.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "PatFox"
#define AppURL       "https://github.com/PatFox/RehearsalRoom"
#define AppExeName   "RehearsalRoom.exe"
#define SourceDir    "..\dist\RehearsalRoom"

[Setup]
AppId={{A3F2B1C4-9E87-4D56-8A23-F1E6D7B90C45}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\installer\output
OutputBaseFilename=RehearsalRoom-v{#AppVersion}-Setup
; SetupIconFile=..\assets\icons\app.ico  (uncomment once app.ico is added)
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
PrivilegesRequired=lowest
; Use 'lowest' so it installs per-user without requiring admin rights.
; Change to 'admin' if you want a machine-wide install in Program Files.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Bundle the entire PyInstaller output folder
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#AppName}";      Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
; Desktop (optional, user-selected above)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the app at the end of installation
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Registry]
; Register .stems file association
Root: HKCU; Subkey: "Software\Classes\.stems";               ValueType: string; ValueName: ""; ValueData: "RehearsalRoom.StemsFile"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\RehearsalRoom.StemsFile";             ValueType: string; ValueName: ""; ValueData: "Rehearsal Room Stems File"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\RehearsalRoom.StemsFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKCU; Subkey: "Software\Classes\RehearsalRoom.StemsFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""

[UninstallDelete]
; Clean up any temp files left by the app
Type: filesandordirs; Name: "{tmp}\rehearsalroom_*"
