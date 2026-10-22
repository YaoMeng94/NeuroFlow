#define AppName "NeuroFlow MRI Film Sheet"
#define AppVersion "0.4.14"

[Setup]
AppId={{3DFEFC2C-9ED0-4770-AD45-C99E0E624971}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NeuroFlow
DefaultDirName={localappdata}\Programs\NeuroFlow MRI Film Sheet
DefaultGroupName=NeuroFlow MRI Film Sheet
PrivilegesRequired=lowest
OutputDir=..\..\..
OutputBaseFilename=NeuroFlowMRIFilmSheet-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\NeuroFlowMRIFilmSheet.exe

[Files]
Source: "..\..\..\dist\film_sheet\NeuroFlowMRIFilmSheet\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NeuroFlow MRI Film Sheet"; Filename: "{app}\NeuroFlowMRIFilmSheet.exe"
Name: "{autodesktop}\NeuroFlow MRI Film Sheet"; Filename: "{app}\NeuroFlowMRIFilmSheet.exe"

[Run]
Filename: "{app}\NeuroFlowMRIFilmSheet.exe"; Description: "Launch NeuroFlow MRI Film Sheet"; Flags: postinstall nowait skipifsilent
