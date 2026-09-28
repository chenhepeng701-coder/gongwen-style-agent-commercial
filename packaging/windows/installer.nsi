Unicode true
!include "MUI2.nsh"
!include "x64.nsh"

!ifndef APP_DIR
!error "APP_DIR must name a verified PyInstaller onedir output"
!endif
!ifndef OUTPUT_FILE
!error "OUTPUT_FILE is required"
!endif
Name "公文写作助手"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\GongwenWritingAssistant"
InstallDirRegKey HKCU "Software\GongwenWritingAssistant" "InstallDir"
RequestExecutionLevel user
SetCompressor zlib
VIProductVersion "0.1.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "公文写作助手"
VIAddVersionKey /LANG=2052 "ProductVersion" "0.1.0"
VIAddVersionKey /LANG=2052 "FileVersion" "0.1.0"
VIAddVersionKey /LANG=2052 "FileDescription" "公文写作助手安装程序"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright 2026. All rights reserved."

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\公文写作助手.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动公文写作助手"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${APP_DIR}\_internal\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  FindWindow $0 "GongwenDesktopControl"
  StrCmp $0 0 +3
    MessageBox MB_ICONSTOP "请先退出公文写作助手，再安装或更新。"
    Abort
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本软件需要 64 位 Windows。"
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Function un.onInit
  FindWindow $0 "GongwenDesktopControl"
  StrCmp $0 0 +3
    MessageBox MB_ICONSTOP "请先退出公文写作助手，再卸载。"
    Abort
FunctionEnd

Section "公文写作助手（必需）" Main
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; Only the verified PyInstaller output is recursively included, never the repository.
  File /r "${APP_DIR}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\公文写作助手"
  CreateShortCut "$SMPROGRAMS\公文写作助手\公文写作助手.lnk" "$INSTDIR\公文写作助手.exe"
  CreateShortCut "$SMPROGRAMS\公文写作助手\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\GongwenWritingAssistant" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "DisplayName" "公文写作助手"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "DisplayVersion" "0.1.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "DisplayIcon" "$INSTDIR\公文写作助手.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant" "NoRepair" 1
SectionEnd

Section "桌面快捷方式" Desktop
  CreateShortCut "$DESKTOP\公文写作助手.lnk" "$INSTDIR\公文写作助手.exe"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ; Explicit program paths only. Never delete LOCALAPPDATA\GongwenWritingAssistant.
  Delete "$INSTDIR\公文写作助手.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\公文写作助手.lnk"
  Delete "$SMPROGRAMS\公文写作助手\公文写作助手.lnk"
  Delete "$SMPROGRAMS\公文写作助手\卸载.lnk"
  RMDir "$SMPROGRAMS\公文写作助手"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant"
  DeleteRegKey HKCU "Software\GongwenWritingAssistant"
SectionEnd
