; MTG Score Tracker - Inno Setup Installer Script
; Requires: Inno Setup 6 (https://jrsoftware.org/isinfo.php)
; Build first with: build\build.bat

#define MyAppName "MTG Score Tracker"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TribalFlames"
#define MyAppURL "https://github.com/TribalFlames/MTG-Score-Tracker"
#define MyAppExeName "MTGScoreTracker.exe"
#define MyOutputDir "..\app-download"
#define MyBuildDir "build\dist\MTGScoreTracker"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=MTGScoreTracker_v{#MyAppVersion}_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
CloseApplications=force
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Main executable and all bundled libs
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Default user data (only if not already present)
Source: "data\config\decks.json"; DestDir: "{userappdata}\MTGScoreTracker\data\config"; Flags: onlyifdoesntexist
Source: "data\config\app_settings.json"; DestDir: "{userappdata}\MTGScoreTracker\data\config"; Flags: onlyifdoesntexist
Source: "data\global\stats.json"; DestDir: "{userappdata}\MTGScoreTracker\data\global"; Flags: onlyifdoesntexist
Source: "data\global\history.md"; DestDir: "{userappdata}\MTGScoreTracker\data\global"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
