; Agent F for Windows: a per-user installer, which also updates an existing install, and its uninstaller.
; tools/build_installer.py compiles it, defining AppVersion, Stage (the bundle folder), Icon and OutputDir.
; The setup work itself is install\install.py, run with the bundled Python once the files are in place.

#define AppName "Agent F"
#define DataDir "{localappdata}\agent-f"

[Setup]
AppId={{5E0C9B54-4A57-4C1E-9F3B-AF52A7B8E1D3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=David Rubino
AppPublisherURL=https://drubino-mozilla.github.io/agent-f/
AppSupportURL=https://github.com/drubino-mozilla/agent-f/issues
AppUpdatesURL=https://drubino-mozilla.github.io/agent-f/
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Agent F
PrivilegesRequired=lowest
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Agent-F-Windows
SetupIconFile={#Icon}
UninstallDisplayIcon={app}\agent-f.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=no

[Messages]
WelcomeLabel2=This installs [name/ver], which lets AI agents work in the Firefox you're already using.%n%nIt sets up a small local server for your Windows account and adds the Agent F add-on to Firefox. It doesn't need administrator rights.%n%nIf Agent F is already installed, this updates it.
FinishedHeadingLabel=Agent F is installed
FinishedLabelNoIcons=Restart Firefox, then enable Agent F from the notice on Firefox's menu button. Then add Agent F to your AI app as an MCP server: "Connect your AI app" at drubino-mozilla.github.io/agent-f explains how.%n%nTo update Agent F later, run a newer installer. To remove it, use Settings > Apps.
FinishedLabel=Restart Firefox, then enable Agent F from the notice on Firefox's menu button. Then add Agent F to your AI app as an MCP server: "Connect your AI app" at drubino-mozilla.github.io/agent-f explains how.%n%nTo update Agent F later, run a newer installer. To remove it, use Settings > Apps.

[Files]
Source: "{#Icon}"; DestDir: "{app}"; DestName: "agent-f.ico"; Flags: ignoreversion
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update starts from clean folders, so files a newer version dropped don't linger.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\install"
Type: filesandordirs; Name: "{app}\host"
Type: filesandordirs; Name: "{app}\addon"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  PauseMarker = 'paused';

function AppPython: String;
begin
  Result := ExpandConstant('{app}\runtime\python.exe');
end;

function InstallScript: String;
begin
  Result := ExpandConstant('{app}\install\install.py');
end;

function NativeDir: String;
begin
  Result := ExpandConstant('{#DataDir}\native');
end;

{ Ends every process running the bundled Python: the broker, and any helpers Firefox started.
  Not the whole install folder, which also holds the uninstaller. }
procedure StopAgentF;
var
  Dir: String;
  ResultCode: Integer;
begin
  Dir := ExpandConstant('{app}\runtime');
  StringChangeEx(Dir, '''', '''''', True);
  Exec('powershell.exe', '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$d = ''' + Dir + '\''; Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ' +
    '$_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

{ On an update, the launcher sees the marker and doesn't start a helper until install.py removes it,
  so nothing locks the runtime while it is replaced. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if DirExists(ExpandConstant('{app}\runtime')) then
  begin
    ForceDirectories(NativeDir);
    SaveStringToFile(NativeDir + '\' + PauseMarker, '', False);
    StopAgentF;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Setting up Agent F and connecting it to Firefox...';
    if not Exec(AppPython, '"' + InstallScript + '" --addon', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode <> 0) then
    begin
      DeleteFile(NativeDir + '\' + PauseMarker);
      SuppressibleMsgBox('Agent F''s files are in place, but setting it up didn''t finish. The details are in ' +
        ExpandConstant('{#DataDir}\logs\install.log') + '.', mbError, MB_OK, IDOK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  Args: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Args := '"' + InstallScript + '" --uninstall';
    if ExpandConstant('{param:purge|0}') = '1' then
      Args := Args + ' --purge'
    else if not UninstallSilent and (MsgBox('Also delete Agent F''s settings and logs?' + #13#10#13#10 +
      'Keep them if you might install Agent F again. The Firefox add-on stays installed either way; ' +
      'you can remove it from about:addons in Firefox.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      Args := Args + ' --purge';
    if FileExists(AppPython) then
      Exec(AppPython, Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    StopAgentF;
  end;
end;
