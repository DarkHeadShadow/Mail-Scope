; Project creator: Bálint Szabolcs
;
; Mail-Scope installer (Inno Setup 6.7). Build from the repository root after
; build_windows.bat:  ISCC /DAppVersion=0.1.0 installer\Mail-Scope.iss
;
; Optional helpers are not bundled: when selected, the newest official
; 7-Zip (GitHub release, SHA-256 from the release) and LibreOffice of the
; Still branch (TDF download server, SHA-256 and digital signature checked)
; are downloaded during setup and installed silently. If that fails, setup
; finishes anyway; Mail-Scope works without them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{CE15FF05-DC92-4617-942C-B394BDC95520}
AppName=Mail-Scope
AppVersion={#AppVersion}
AppVerName=Mail-Scope {#AppVersion}
AppPublisher=Bálint Szabolcs
DefaultDirName={autopf}\Mail-Scope
DefaultGroupName=Mail-Scope
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=..\assets\logo\mail-scope.ico
UninstallDisplayIcon={app}\Mail-Scope.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=Mail-Scope-Setup-{#AppVersion}-x64

[Languages]
Name: "hu"; MessagesFile: "compiler:Languages\Hungarian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ikon az asztalon"; Flags: unchecked
Name: "sevenzip"; Description: "7-Zip letöltése és telepítése (RAR-archívumokhoz; a legfrissebb hivatalos kiadás, kb. 2 MB)"; Check: NeedSevenZip
Name: "libreoffice"; Description: "LibreOffice letöltése és telepítése a „Pontos nézethez” (Still ág, kb. 370 MB)"; Check: NeedLibreOffice

[Files]
Source: "..\dist\Mail-Scope\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "downloads.ps1"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Mail-Scope"; Filename: "{app}\Mail-Scope.exe"
Name: "{autodesktop}\Mail-Scope"; Filename: "{app}\Mail-Scope.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Mail-Scope.exe"; Description: "A Mail-Scope indítása"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  Problems: String;
  CurrentDownload: String;

function SevenZipInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\7-Zip\7z.exe')) or
    FileExists(ExpandConstant('{commonpf32}\7-Zip\7z.exe'));
end;

function LibreOfficeInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\LibreOffice\program\soffice.exe')) or
    FileExists(ExpandConstant('{commonpf32}\LibreOffice\program\soffice.exe'));
end;

function NeedSevenZip: Boolean;
begin
  Result := not SevenZipInstalled;
end;

function NeedLibreOffice: Boolean;
begin
  Result := not LibreOfficeInstalled;
end;

procedure AddProblem(const Text: String);
begin
  Log('Problem: ' + Text);
  Problems := Problems + #13#10 + '• ' + Text;
end;

{ The Index-th (0-based) field of a "|"-separated line. }
function Field(Line: String; Index: Integer): String;
var
  Position: Integer;
begin
  while Index > 0 do
  begin
    Position := Pos('|', Line);
    if Position = 0 then
    begin
      Result := '';
      Exit;
    end;
    Delete(Line, 1, Position);
    Index := Index - 1;
  end;
  Position := Pos('|', Line);
  if Position > 0 then
    Result := Copy(Line, 1, Position - 1)
  else
    Result := Line;
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
  begin
    WizardForm.ProgressGauge.Max := 1000;
    WizardForm.ProgressGauge.Position := Integer(Progress * 1000 div ProgressMax);
    WizardForm.StatusLabel.Caption := Format('%s letöltése… %d / %d MB',
      [CurrentDownload, Integer(Progress div 1048576), Integer(ProgressMax div 1048576)]);
  end;
  Result := True;
end;

function PowerShell(const Arguments: String; var ResultCode: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\downloads.ps1') + '" ' + Arguments,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Download one resolved line (NAME|url|sha256|version); return the local MSI path or ''. }
function Download(const Line, Title, FileName: String): String;
begin
  Result := '';
  CurrentDownload := Title + ' ' + Field(Line, 3);
  WizardForm.StatusLabel.Caption := CurrentDownload + ' letöltése…';
  try
    { Inno Setup checks the SHA-256 and refuses a file that does not match. }
    DownloadTemporaryFile(Field(Line, 1), FileName, Field(Line, 2), @OnDownloadProgress);
    Result := ExpandConstant('{tmp}\' + FileName);
  except
    AddProblem(Title + ': a letöltés nem sikerült (' + GetExceptionMessage + ').');
  end;
end;

procedure InstallMsi(const Title, Msi, Properties: String);
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := Title + ' telepítése… (ez néhány percig tarthat)';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if not Exec(ExpandConstant('{sys}\msiexec.exe'), '/i "' + Msi + '" /qn /norestart ' + Properties,
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      AddProblem(Title + ': a telepítő nem indult el.')
    else if (ResultCode <> 0) and (ResultCode <> 3010) and (ResultCode <> 1641) then
      AddProblem(Format('%s: a telepítés hibakóddal ért véget (%d).', [Title, ResultCode]))
    else
      Log(Title + ' installed, msiexec exit code ' + IntToStr(ResultCode));
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

procedure InstallHelpers;
var
  Arguments, Msi: String;
  Lines: TArrayOfString;
  ResultCode, Index: Integer;
begin
  Arguments := '';
  if WizardIsTaskSelected('sevenzip') then
    Arguments := Arguments + ' -SevenZip';
  if WizardIsTaskSelected('libreoffice') then
    Arguments := Arguments + ' -LibreOffice';
  if Arguments = '' then
    Exit;
  WizardForm.StatusLabel.Caption := 'A legfrissebb 7-Zip és LibreOffice keresése…';
  ExtractTemporaryFile('downloads.ps1');
  if not PowerShell('-Resolve -OutFile "' + ExpandConstant('{tmp}\downloads.txt') + '"' + Arguments, ResultCode)
    or not LoadStringsFromFile(ExpandConstant('{tmp}\downloads.txt'), Lines) then
  begin
    AddProblem('A letöltések nem kereshetők meg (nincs internetkapcsolat?).');
    Exit;
  end;
  for Index := 0 to GetArrayLength(Lines) - 1 do
  begin
    if Field(Lines[Index], 0) = 'SEVENZIP' then
    begin
      Msi := Download(Lines[Index], '7-Zip', '7zip.msi');
      if Msi <> '' then
        InstallMsi('7-Zip', Msi, '');
    end
    else if Field(Lines[Index], 0) = 'LIBREOFFICE' then
    begin
      Msi := Download(Lines[Index], 'LibreOffice', 'libreoffice.msi');
      if Msi <> '' then
      begin
        WizardForm.StatusLabel.Caption := 'A LibreOffice digitális aláírásának ellenőrzése…';
        if PowerShell('-VerifySignature "' + Msi + '" -Publisher "The Document Foundation"', ResultCode)
          and (ResultCode = 0) then
          { Keep Microsoft Office as the default for .docx/.xlsx; no desktop link. }
          InstallMsi('LibreOffice', Msi, 'REGISTER_NO_MSO_TYPES=1 CREATEDESKTOPLINK=0 RebootYesNo=No')
        else
          AddProblem('LibreOffice: a letöltött telepítő digitális aláírása nem érvényes, ezért nem telepítettük.');
      end;
    end
    else if Field(Lines[Index], 1) <> '' then
      AddProblem(Field(Lines[Index], 1));
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InstallHelpers;
    if Problems <> '' then
      SuppressibleMsgBox('A Mail-Scope telepítése elkészült, de a kiegészítők közül nem minden sikerült:' + #13#10 +
        Problems + #13#10#13#10 + 'A Mail-Scope enélkül is működik. A 7-Zip és a LibreOffice később ' +
        'a hivatalos oldalukról is telepíthető; a Mail-Scope megtalálja őket.', mbInformation, MB_OK, IDOK);
  end;
end;
