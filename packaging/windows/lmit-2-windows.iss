#define AppName "LMIT-2 Wiki"
#define AppVersion "0.1.0"
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
Name: "schedule"; Description: "Create Windows scheduled ingest, sync, and lint tasks"; GroupDescription: "Automation:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\lmit-wiki\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "scripts\*.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\..\config\wiki-only.windows.example.toml"; DestDir: "{app}\config"; Flags: ignoreversion

[Icons]
Name: "{group}\LMIT-2 Wiki Console"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\start-console.ps1"" -InstallDir ""{app}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"""
Name: "{group}\LMIT-2 CLI Help"; Filename: "cmd.exe"; Parameters: "/K ""{app}\lmit-wiki.exe"" --help"
Name: "{group}\LMIT-2 Ingest Now"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\ingest-now.ps1"" -InstallDir ""{app}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"""
Name: "{group}\LMIT-2 Sync Now"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\sync-now.ps1"" -InstallDir ""{app}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"""
Name: "{group}\LMIT-2 Lint Now"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\lint-now.ps1"" -InstallDir ""{app}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"""
Name: "{autodesktop}\LMIT-2 Wiki Console"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\start-console.ps1"" -InstallDir ""{app}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"""; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\init-windows-install.ps1"" -InstallDir ""{app}"" -KnowledgeBaseRoot ""{code:GetKnowledgeBaseRoot}"" -RawSourceDir ""{code:GetRawSourceDir}"" -ConfigPath ""{userappdata}\LMIT-2\wiki-only.toml"" -InstallTasks:{code:GetInstallTasks}"; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\remove-scheduled-tasks.ps1"""; Flags: runhidden waituntilterminated

[Code]
var
  DataPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(
    wpSelectDir,
    'Choose LMIT-2 data folders',
    'Select the local knowledge base folder and the LMIT-1 raw Markdown folder.',
    'LMIT-2 stores its generated wiki in the knowledge base folder. The raw source folder is read-only input from LMIT-1.',
    False,
    ''
  );
  DataPage.Add('Knowledge base folder:');
  DataPage.Add('LMIT-1 raw Markdown folder:');
  DataPage.Values[0] := ExpandConstant('{userdocs}\LMIT-2\knowledge_base');
  DataPage.Values[1] := ExpandConstant('{userdocs}\LMIT\output\raw');
end;

function GetKnowledgeBaseRoot(Param: string): string;
begin
  Result := DataPage.Values[0];
end;

function GetRawSourceDir(Param: string): string;
begin
  Result := DataPage.Values[1];
end;

function GetInstallTasks(Param: string): string;
begin
  if WizardIsTaskSelected('schedule') then
    Result := '$true'
  else
    Result := '$false';
end;
