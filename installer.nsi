; 全屏提示 安装脚本（NSIS 3.x，当前用户安装，无需管理员权限）
; 构建：tools\nsis\nsis-3.10\makensis.exe /V2 "/DPROJECTDIR=<项目目录>" installer.nsi
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define APP_NAME "全屏提示"
!define APP_EXE "Banner.exe"
!define APP_VER "1.4"
!define APP_PUB "Banner"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BannerOverlay"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define RUN_VALUE "全屏提示"

!ifndef PROJECTDIR
  !define PROJECTDIR "."
!endif

Name "${APP_NAME}"
OutFile "${PROJECTDIR}\dist\installer\全屏提示_Setup_${APP_VER}.exe"
InstallDir "$LOCALAPPDATA\Programs\Banner"
InstallDirRegKey HKCU "Software\BannerOverlay" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APP_NAME} 安装程序"

!define MUI_ABORTWARNING
!define MUI_ICON "${PROJECTDIR}\tray.ico"
!define MUI_UNICON "${PROJECTDIR}\tray.ico"
!define MUI_FINISHPAGE_NOAUTOCLOSE
!define MUI_FINISHPAGE_TEXT "${APP_NAME} 已安装完成。$\r$\n$\r$\n托盘出现金色锁头图标后：$\r$\n  • 左键单击托盘图标：显示 / 关闭横幅$\r$\n  • 横幅显示时按 Esc 关闭；按 Ctrl+Alt+Shift+Esc 可强制解除键盘屏蔽$\r$\n  • 右键托盘菜单：选择自定义图片、适配方式、重新出图、开机启动、退出$\r$\n$\r$\n配置与日志保留在 %LOCALAPPDATA%\Banner，卸载时不会删除。"
!define MUI_UNTEXT_CONFIRM_TEXT "将卸载 ${APP_NAME}，并删除程序文件与快捷方式。$\r$\n$\r$\n配置、日志和自动生成的图片（%LOCALAPPDATA%\Banner）会被保留。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

VIProductVersion "${APP_VER}.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VER}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VER}"
VIAddVersionKey /LANG=2052 "CompanyName" "${APP_PUB}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "${APP_PUB}"

Section "主程序（必需）" SEC_CORE
  SectionIn RO
  ; 覆盖安装前先结束正在运行的实例，避免文件占用
  nsExec::ExecToLog 'taskkill /F /IM ${APP_EXE}'
  Sleep 500
  SetOutPath "$INSTDIR"
  File /r "${PROJECTDIR}\dist\Banner\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\BannerOverlay" "InstallDir" "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VER}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUB}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 43000
SectionEnd

Section "创建桌面快捷方式" SEC_DESKTOP
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "开机启动（推荐）" SEC_AUTOSTART
  WriteRegStr HKCU "${RUN_KEY}" "${RUN_VALUE}" '"$INSTDIR\${APP_EXE}" --quiet'
SectionEnd

Section "安装完成后立即启动" SEC_RUN
  Exec '"$INSTDIR\${APP_EXE}"'
SectionEnd

Function un.onInit
  SetShellVarContext current
FunctionEnd

Section "Uninstall"
  nsExec::ExecToLog 'taskkill /F /IM ${APP_EXE}'
  Sleep 500
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  DeleteRegValue HKCU "${RUN_KEY}" "${RUN_VALUE}"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\BannerOverlay"
  RMDir /r "$INSTDIR"
SectionEnd

Function .onInit
  SetShellVarContext current
  ; 静默安装（/S）不碰桌面快捷方式、不开机自启、不自动启动，方便自动化验证
  ${If} ${Silent}
    SectionSetFlags ${SEC_DESKTOP} 0
    SectionSetFlags ${SEC_AUTOSTART} 0
    SectionSetFlags ${SEC_RUN} 0
  ${EndIf}
FunctionEnd
