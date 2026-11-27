; Inno Setup script for More Simple FTP.
; Build with:  iscc /DAppVersion=1.0.0 installer\more-simple-ftp.iss
; Expects PyInstaller output in dist\ (see build.ps1).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{7E3C2B4A-5D1F-4F8E-9A63-2C1B0D9E4F71}
AppName=More Simple FTP
AppVersion={#AppVersion}
AppPublisher=Hamilton Group Programming
DefaultDirName={autopf}\More Simple FTP
DefaultGroupName=More Simple FTP
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=MoreSimpleFTP-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MoreSimpleFTP.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MoreSimpleFTP.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ftpclient.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\More Simple FTP"; Filename: "{app}\MoreSimpleFTP.exe"
Name: "{autodesktop}\More Simple FTP"; Filename: "{app}\MoreSimpleFTP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MoreSimpleFTP.exe"; Description: "{cm:LaunchProgram,More Simple FTP}"; Flags: nowait postinstall skipifsilent
