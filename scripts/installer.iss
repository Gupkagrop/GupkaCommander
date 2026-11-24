; =====================================================================
; Спецификация сборщика инсталлятора Inno Setup для GupkaCommander
; =====================================================================

#define MyAppName "GupkaCommander"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Gupkagrop"
#define MyAppURL "https://github.com/Gupkagrop/GupkaCommander"
#define MyAppExeName "GupkaCommander.exe"

[Setup]
; Уникальный GUID приложения
AppId={{9C1E26A4-0B5E-4B38-89F1-ED7E6F8E80C2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Гибкий выбор каталога: {autopf} разрешает {localappdata}\Programs (user-mode)
; либо Program Files (admin-mode)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=GupkaCommander-Setup-v{#MyAppVersion}
SetupIconFile=..\assets\gupka_commander.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; Двухуровневый режим: по умолчанию lowest (без UAC), с диалогом выбора
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\assets\gupka_commander.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать GupkaCommander при старте Windows"; GroupDescription: "Автозапуск:"; Flags: unchecked

[Files]
Source: "..\dist\GupkaCommander\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\gupka_commander.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\gupka_commander.ico"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--quiet"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Удаляем скачанные модели и llama-server при деинсталляции, сохраняя config\memory.db и settings.json
Type: filesandordirs; Name: "{localappdata}\GupkaCommander\models"
Type: filesandordirs; Name: "{localappdata}\GupkaCommander\bin"
