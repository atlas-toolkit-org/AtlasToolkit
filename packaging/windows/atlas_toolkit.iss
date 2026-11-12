; The Windows installer, made by scripts/package.py from the folder build:
;   ISCC /DVersion=... /DSource=... /DOutput=... /DRoot=... /DIcon=... packaging\windows\atlas_toolkit.iss
;
; It installs for the current user without asking for an administrator, or for all users if chosen. Upgrading installs over
; the old version in place: the AppId must never change, and the old payload goes first, so that no file of an older version
; stays mixed in. Uninstalling leaves settings, presets and history alone: they are in the user's profile, not here. The GPL
; helper's source and notices are inside the app folder already (_internal\atlas_toolkit\licenses).

#ifndef Version
  #error Pass /DVersion=... (scripts/package.py does)
#endif

[Setup]
AppId={{7C1E5A52-6D0B-4B7E-9C1E-3F0B8D2A4E11}
AppName=Atlas Toolkit
AppVersion={#Version}
AppVerName=Atlas Toolkit {#Version}
VersionInfoVersion={#Version}
VersionInfoProductName=Atlas Toolkit
VersionInfoDescription=Atlas Toolkit installer
VersionInfoCopyright=Copyright (C) 2026 Joao Goncalves (sci.co.plot) and contributors. GPL-3.0-or-later.
AppPublisher=sci.co.plot
AppPublisherURL=https://atlas-toolkit.org
AppSupportURL=https://atlas-toolkit.org
AppUpdatesURL=https://atlas-toolkit.org
; No LicenseFile page on purpose: the GPL is not an agreement to accept before installing. LICENSE ships next to the app.
DefaultDirName={autopf}\Atlas Toolkit
DefaultGroupName=Atlas Toolkit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=AtlasToolkit-{#Version}-windows-x64-setup
SetupIconFile={#Icon}
UninstallDisplayIcon={app}\Atlas Toolkit.exe
UninstallDisplayName=Atlas Toolkit
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
Name: association; Description: "Open .atlasflow workflows, .atlaspreset presets and .atlaspack preset packs with Atlas Toolkit"
Name: path; Description: "Add atlas-toolkit to PATH, for the command line"; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Atlas Toolkit"; Filename: "{app}\Atlas Toolkit.exe"
Name: "{autodesktop}\Atlas Toolkit"; Filename: "{app}\Atlas Toolkit.exe"; Tasks: desktopicon

[Registry]
; HKA is the current user's hive or the machine's, whichever this install is for.
Root: HKA; Subkey: "Software\Classes\.atlasflow"; ValueType: string; ValueName: ""; ValueData: "AtlasToolkit.Workflow"; Flags: uninsdeletevalue; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Workflow"; ValueType: string; ValueName: ""; ValueData: "Atlas Toolkit workflow"; Flags: uninsdeletekey; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Workflow\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Atlas Toolkit.exe,0"; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Workflow\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Atlas Toolkit.exe"" ""%1"""; Tasks: association
Root: HKA; Subkey: "Software\Classes\.atlaspreset"; ValueType: string; ValueName: ""; ValueData: "AtlasToolkit.Preset"; Flags: uninsdeletevalue; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Preset"; ValueType: string; ValueName: ""; ValueData: "Atlas Toolkit presets"; Flags: uninsdeletekey; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Preset\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Atlas Toolkit.exe,0"; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.Preset\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Atlas Toolkit.exe"" ""%1"""; Tasks: association
Root: HKA; Subkey: "Software\Classes\.atlaspack"; ValueType: string; ValueName: ""; ValueData: "AtlasToolkit.PresetPack"; Flags: uninsdeletevalue; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.PresetPack"; ValueType: string; ValueName: ""; ValueData: "Atlas Toolkit preset pack"; Flags: uninsdeletekey; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.PresetPack\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Atlas Toolkit.exe,0"; Tasks: association
Root: HKA; Subkey: "Software\Classes\AtlasToolkit.PresetPack\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Atlas Toolkit.exe"" ""%1"""; Tasks: association

[Run]
Filename: "{app}\Atlas Toolkit.exe"; Description: "Start Atlas Toolkit"; Flags: nowait postinstall skipifsilent

[Code]
const
  EnvKeyUser = 'Environment';
  EnvKeyMachine = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

function PathRoot: Integer;
begin
  if IsAdminInstallMode then Result := HKEY_LOCAL_MACHINE else Result := HKEY_CURRENT_USER;
end;

function PathKey: String;
begin
  if IsAdminInstallMode then Result := EnvKeyMachine else Result := EnvKeyUser;
end;

// Our folder as one entry of PATH, wherever it stands in the list.
function WithoutApp(Value, App: String): String;
var
  Haystack: String;
  At: Integer;
begin
  Haystack := ';' + Value + ';';
  At := Pos(';' + Lowercase(App) + ';', Lowercase(Haystack));
  if At > 0 then
    Delete(Haystack, At, Length(App) + 1);
  Result := Haystack;
  if (Length(Result) > 0) and (Result[1] = ';') then
    Delete(Result, 1, 1);
  if (Length(Result) > 0) and (Result[Length(Result)] = ';') then
    Delete(Result, Length(Result), 1);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Value: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('path') then
  begin
    if not RegQueryStringValue(PathRoot, PathKey, 'Path', Value) then
      Value := '';
    Value := WithoutApp(Value, ExpandConstant('{app}'));
    if Value <> '' then
      Value := Value + ';';
    RegWriteExpandStringValue(PathRoot, PathKey, 'Path', Value + ExpandConstant('{app}'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value, Cleaned: String;
begin
  if (CurUninstallStep = usPostUninstall) and RegQueryStringValue(PathRoot, PathKey, 'Path', Value) then
  begin
    Cleaned := WithoutApp(Value, ExpandConstant('{app}'));
    if Cleaned <> Value then
      RegWriteExpandStringValue(PathRoot, PathKey, 'Path', Cleaned);
  end;
end;
