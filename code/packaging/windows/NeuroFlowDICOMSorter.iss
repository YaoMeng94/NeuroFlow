#define AppName "NeuroFlow DICOM Sorter"
#define AppVersion "0.4.15"

[Setup]
AppId={{EBD8AD75-1289-4F50-A5E9-2E1D7F41E224}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NeuroFlow
DefaultDirName={localappdata}\Programs\NeuroFlow DICOM Sorter
DefaultGroupName=NeuroFlow DICOM Sorter
PrivilegesRequired=lowest
OutputDir=..\..\..
OutputBaseFilename=NeuroFlowDICOMSorter-Windows-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\NeuroFlowDICOMSorter.exe

[Files]
Source: "..\..\..\dist\standalone_sorter\NeuroFlowDICOMSorter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NeuroFlow DICOM Sorter"; Filename: "{app}\NeuroFlowDICOMSorter.exe"
Name: "{autodesktop}\NeuroFlow DICOM Sorter"; Filename: "{app}\NeuroFlowDICOMSorter.exe"

[Run]
Filename: "{app}\NeuroFlowDICOMSorter.exe"; Description: "Launch NeuroFlow DICOM Sorter"; Flags: postinstall nowait skipifsilent
