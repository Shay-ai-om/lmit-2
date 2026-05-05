#define AppName "LMIT-2 Wiki"
#define AppVersion "0.1.10"
#define AppPublisher "LMIT"

[Setup]
AppId={{4E747C38-624B-49E6-985A-2B54F27CF7CF}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\LMIT-2 Wiki
DefaultGroupName=LMIT-2 Wiki
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=LMIT-2-Wiki-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "schedule"; Description: "Create Windows scheduled ingest, sync, and lint tasks"; GroupDescription: "Automation:"; Flags: unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\lmit-wiki\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "scripts\*.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\*.cmd"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion
Source: "..\..\config\wiki-only.windows.example.toml"; DestDir: "{app}\config"; Flags: ignoreversion
Source: "..\..\docs\web-ui-guide.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\LMIT-2 Wiki Console"; Filename: "{app}\scripts\start-console.cmd"; WorkingDir: "{app}"
Name: "{group}\LMIT-2 CLI Help"; Filename: "cmd.exe"; Parameters: "/K ""{app}\lmit-wiki.exe"" --help"
Name: "{autodesktop}\LMIT-2 Wiki Console"; Filename: "{app}\scripts\start-console.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\init-windows-install.ps1"" -InstallDir ""{app}"" -KnowledgeBaseRoot ""{code:GetKnowledgeBaseRoot}"" -RawSourceDir ""{code:GetRawSourceDir}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"" -InstallTasks:{code:GetInstallTasks}"; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\remove-scheduled-tasks.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveLmitWikiScheduledTasks"

[Code]
function GetKnowledgeBaseRoot(Param: string): string;
begin
  Result := ExpandConstant('{userdocs}\LMIT-2\knowledge_base');
end;

function GetRawSourceDir(Param: string): string;
begin
  Result := ExpandConstant('{userdocs}\LMIT\output\raw');
end;

function GetInstallTasks(Param: string): string;
begin
  if WizardIsTaskSelected('schedule') then
    Result := '$true'
  else
    Result := '$false';
end;
