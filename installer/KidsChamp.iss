#define MyAppName "KidsChamp"
#define MyAppVersion "1.1"
#define MyAppPublisher "Rusala Media"
#define MyAppExeName "KidsChamp.exe"
#ifndef BuildRoot
  #define BuildRoot "..\build_pyinstaller_v1_1\KidsChamp"
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif

[Setup]
AppId={{1CC4B4F6-9D4A-4F51-A69E-0EA65F741B43}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\KidsChamp
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=KidsChamp_Setup_V1_1
SetupIconFile={#ProjectRoot}\assets\kidsChamp.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
AppMutex=KidsChampArtworkGeneratorAutoV4

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Excludes: "_internal\app_data\settings.json,_internal\*.dist-info\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\app_data\settings.json"; DestDir: "{app}\_internal\app_data"; Flags: onlyifdoesntexist

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
