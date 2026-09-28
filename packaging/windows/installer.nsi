Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WordFunc.nsh"

!ifndef APP_DIR
!error "APP_DIR must name a verified PyInstaller onedir output"
!endif
!ifndef OUTPUT_FILE
!error "OUTPUT_FILE is required"
!endif
!ifndef WEBVIEW2_INSTALLER
!error "WEBVIEW2_INSTALLER must name the SHA-256/signature-verified offline x64 installer"
!endif
!ifndef WEBVIEW2_MIN_RUNTIME
!error "WEBVIEW2_MIN_RUNTIME must come from desktop_shell.py via build_release.py"
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
!insertmacro MUI_PAGE_LICENSE "${APP_DIR}\_internal\licenses\Microsoft-WebView2-Evergreen-LICENSE.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

!macro RequireStopped
  ; The launcher holds this file open for its whole lifetime, including startup.
  System::Call 'kernel32::CreateFileW(w "$LOCALAPPDATA\GongwenWritingAssistant\config\desktop.lock", i 0x80000000, i 0, p 0, i 3, i 0, p 0) p.r0'
  ${If} $0 == -1
    System::Call 'kernel32::GetLastError() i.r1'
    ${If} $1 == 32
    ${OrIf} $1 == 33
      MessageBox MB_ICONSTOP "请先退出公文写作助手，再安装、更新或卸载。"
      Abort
    ${EndIf}
  ${Else}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
!macroend

Function .onInit
  !insertmacro RequireStopped
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本软件需要 64 位 Windows。"
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Function un.onInit
  !insertmacro RequireStopped
FunctionEnd

Function DetectWebView2
  SetRegView 32
  ReadRegStr $1 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${VersionCompare} $1 "${WEBVIEW2_MIN_RUNTIME}" $0
  ${If} $1 != ""
  ${AndIf} $0 != 2
    StrCpy $0 1
    Return
  ${EndIf}
  ReadRegStr $1 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${VersionCompare} $1 "${WEBVIEW2_MIN_RUNTIME}" $0
  ${If} $1 != ""
  ${AndIf} $0 != 2
    StrCpy $0 1
  ${Else}
    StrCpy $0 0
  ${EndIf}
FunctionEnd

Section "公文写作助手（必需）" Main
  SectionIn RO
  Call DetectWebView2
  ${If} $0 == 0
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File /oname=MicrosoftEdgeWebView2RuntimeInstallerX64.exe "${WEBVIEW2_INSTALLER}"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" /silent /install' $2
    Call DetectWebView2
    ${If} $0 == 0
      MessageBox MB_ICONSTOP "WebView2 运行环境安装未完成（返回码 $2）。请联系支持人员。"
      Abort
    ${EndIf}
  ${EndIf}
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
